import asyncio

import httpx
import httpx2
import pytest
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken
from monarch_proxy.auth import (
    MonarchAuthRequired,
    build_client_metadata,
    force_refresh,
    make_provider,
    wait_for_callback,
    with_refresh_retry,
)
from monarch_proxy.tokens import FileTokenStorage


def test_metadata_is_read_only():
    m = build_client_metadata()
    assert m.client_name == "Monti (read-only)"
    assert m.scope == "mcp:read"
    assert [str(u) for u in m.redirect_uris] == ["http://127.0.0.1:8765/callback"]
    assert m.token_endpoint_auth_method == "none"
    assert set(m.grant_types) == {"authorization_code", "refresh_token"}


def test_metadata_scope_cannot_be_widened():
    # The SDK overwrites client_metadata.scope with server-advertised scopes during a 401 flow.
    m = build_client_metadata()
    m.scope = "mcp:read mcp:write"
    assert m.scope == "mcp:read"


async def test_noninteractive_provider_raises_on_redirect(tmp_path):
    p = make_provider(FileTokenStorage(tmp_path / "t.json"), interactive=False)
    with pytest.raises(MonarchAuthRequired):
        await p.context.redirect_handler("https://example/authorize")
    with pytest.raises(MonarchAuthRequired):
        await p.context.callback_handler()


async def test_interactive_redirect_forces_read_scope(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr("webbrowser.open", opened.append)
    p = make_provider(FileTokenStorage(tmp_path / "t.json"), interactive=True)
    await p.context.redirect_handler(
        "https://x/authorize/?client_id=c&scope=mcp%3Aread+mcp%3Awrite"
    )
    assert "mcp%3Awrite" not in opened[0]
    assert "scope=mcp%3Aread" in opened[0]


async def test_callback_server_captures_code():
    task = asyncio.create_task(wait_for_callback(18765))
    async with httpx.AsyncClient() as c:
        for _ in range(50):
            try:
                r = await c.get("http://127.0.0.1:18765/callback?code=abc&state=xyz")
                break
            except httpx.ConnectError:
                await asyncio.sleep(0.05)
    assert r.status_code == 200
    assert await task == ("abc", "xyz")


TOKEN_URL = "https://auth.example/oauth/token/"


async def _seeded_provider(tmp_path, refresh_status=200):
    storage = FileTokenStorage(tmp_path / "t.json")
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://127.0.0.1:8765/callback"],
            token_endpoint_auth_method="none",
        )
    )
    await storage.set_tokens(
        OAuthToken(access_token="bogus", token_type="Bearer", refresh_token="r1", expires_in=3600)
    )
    p = make_provider(storage, interactive=False)
    # Normally populated by the SDK's discovery step before it calls the redirect handler.
    p.context.oauth_metadata = OAuthMetadata(
        issuer="https://auth.example",
        authorization_endpoint="https://auth.example/oauth/authorize/",
        token_endpoint=TOKEN_URL,
    )
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if refresh_status != 200:
            return httpx2.Response(refresh_status, json={"error": "invalid_grant"})
        return httpx2.Response(
            200, json={"access_token": "fresh", "token_type": "Bearer", "expires_in": 3600}
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return p, storage, client, seen


async def test_force_refresh_uses_refresh_token_and_persists(tmp_path):
    p, _, client, seen = await _seeded_provider(tmp_path)
    async with client:
        assert await force_refresh(p, client=client) is True
    assert str(seen[0].url) == TOKEN_URL
    body = seen[0].content.decode()
    assert "grant_type=refresh_token" in body and "refresh_token=r1" in body
    stored = await FileTokenStorage(tmp_path / "t.json").get_tokens()
    assert stored.access_token == "fresh"
    assert stored.refresh_token == "r1"  # not rotated by server -> carried forward


async def test_force_refresh_failure_returns_false(tmp_path):
    p, _, client, _ = await _seeded_provider(tmp_path, refresh_status=400)
    async with client:
        assert await force_refresh(p, client=client) is False


async def test_with_refresh_retry_retries_once(tmp_path):
    p, storage, client, _ = await _seeded_provider(tmp_path)
    calls = []

    async def op():
        calls.append((await storage.get_tokens()).access_token)
        if len(calls) == 1:
            raise ExceptionGroup("wrapped", [MonarchAuthRequired("401")])
        return "ok"

    async with client:
        assert await with_refresh_retry(p, op, client=client) == "ok"
    assert calls == ["bogus", "fresh"]


async def test_with_refresh_retry_gives_up_when_refresh_fails(tmp_path):
    p, _, client, _ = await _seeded_provider(tmp_path, refresh_status=400)

    async def op():
        raise MonarchAuthRequired("401")

    async with client:
        with pytest.raises(MonarchAuthRequired):
            await with_refresh_retry(p, op, client=client)


async def test_refresh_rereads_storage_after_failed_refresh(tmp_path):
    # Bogus tokens -> refresh fails (SDK clears in-memory tokens). The owner then runs
    # `mise run monarch-login` in another process, which rewrites tokens.json. The same,
    # long-lived provider must pick the new tokens up without a proxy restart.
    p, _, _, _ = await _seeded_provider(tmp_path)
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.content.decode())
        if "refresh_token=r1" in seen[-1]:
            return httpx2.Response(400, json={"error": "invalid_grant"})
        return httpx2.Response(
            200, json={"access_token": "fresh2", "token_type": "Bearer", "expires_in": 3600}
        )

    async def failing_op():
        raise MonarchAuthRequired("401")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(MonarchAuthRequired):
            await with_refresh_retry(p, failing_op, client=client)

        await FileTokenStorage(tmp_path / "t.json").set_tokens(
            OAuthToken(access_token="relogin", token_type="Bearer", refresh_token="r2")
        )

        calls = []

        async def op():
            calls.append(
                p.context.current_tokens.access_token if p.context.current_tokens else None
            )
            if len(calls) == 1:
                raise MonarchAuthRequired("401")
            return "ok"

        assert await with_refresh_retry(p, op, client=client) == "ok"
    assert "refresh_token=r2" in seen[-1]
    assert calls[-1] == "fresh2"
