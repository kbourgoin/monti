"""Monarch MCP OAuth: provider construction, loopback callback, forced refresh."""

import asyncio
import webbrowser
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

import httpx2
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientMetadata

from monarch_proxy.tokens import FileTokenStorage

MONARCH_MCP_URL = "https://api.monarch.com/mcp"
REDIRECT_PORT = 8765
READ_SCOPE = "mcp:read"


class MonarchAuthRequired(Exception):
    """Stored credentials are missing/unusable and interactive login is needed."""


class _ReadOnlyScopeMetadata(OAuthClientMetadata):
    """Client metadata whose scope is pinned to `mcp:read`.

    The SDK's 401 flow overwrites `client_metadata.scope` with whatever the server advertises
    (WWW-Authenticate / PRM / AS `scopes_supported`, which for Monarch includes `mcp:write`),
    and that value is then sent in both dynamic registration and the authorize URL. Ignoring
    assignments keeps the requested scope at exactly `mcp:read`.
    """

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "scope":
            return
        super().__setattr__(name, value)


def build_client_metadata() -> OAuthClientMetadata:
    return _ReadOnlyScopeMetadata(
        client_name="mm-text Signal bot (read-only)",
        redirect_uris=[f"http://127.0.0.1:{REDIRECT_PORT}/callback"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
        scope=READ_SCOPE,
    )


_PAGE = b"<html><body><p>Monarch login complete. You can close this tab.</p></body></html>"


async def wait_for_callback(port: int) -> tuple[str, str | None]:
    """Run a one-shot loopback HTTP server; return `(code, state)` from `/callback`."""
    loop = asyncio.get_running_loop()
    result: asyncio.Future[tuple[str, str | None]] = loop.create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await reader.readline()).decode("latin-1")
            parts = line.split()
            target = urlsplit(parts[1]) if len(parts) >= 2 else urlsplit("")
            q = parse_qs(target.query)
            code = q.get("code", [None])[0]
            if target.path == "/callback" and code:
                status, body = "200 OK", _PAGE
                if not result.done():
                    result.set_result((code, q.get("state", [None])[0]))
            elif target.path == "/callback" and not result.done():
                status, body = "400 Bad Request", b"Missing authorization code."
                result.set_exception(MonarchAuthRequired(f"authorization failed: {q}"))
            else:
                status, body = "404 Not Found", b"Not found."
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: text/html\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    try:
        return await result
    finally:
        server.close()
        await server.wait_closed()


def _force_read_scope(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "scope"]
    query.append(("scope", READ_SCOPE))
    return urlunsplit(parts._replace(query=urlencode(query)))


def make_provider(
    storage: FileTokenStorage, *, interactive: bool, server_url: str = MONARCH_MCP_URL
) -> OAuthClientProvider:
    async def redirect_handler(url: str) -> None:
        if not interactive:
            raise MonarchAuthRequired("Monarch login required")
        url = _force_read_scope(url)
        print(
            f"Opening browser to authorize Monarch (read-only). If it does not open, visit:\n{url}"
        )
        webbrowser.open(url)

    async def callback_handler() -> AuthorizationCodeResult:
        if not interactive:
            raise MonarchAuthRequired("Monarch login required")
        code, state = await wait_for_callback(REDIRECT_PORT)
        return AuthorizationCodeResult(code=code, state=state)

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=build_client_metadata(),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )


async def force_refresh(
    provider: OAuthClientProvider, *, client: httpx2.AsyncClient | None = None
) -> bool:
    """Refresh the access token with the stored refresh token, regardless of expiry.

    The SDK neither refreshes a stored-but-expired token on load nor on a 401 (it jumps to full
    re-authorization), so callers use this to recover. Needs `context.oauth_metadata` for the
    token endpoint, which the SDK's 401 discovery step fills in before it invokes the redirect
    handler. Returns False when there is nothing to refresh with or the server rejects it.

    Storage is always re-read first: the provider lives as long as the proxy, and after a failed
    refresh the SDK drops its in-memory tokens but never reloads them, so a `make monarch-login`
    in another process would otherwise go unnoticed until restart.
    """
    await provider._initialize()
    ctx = provider.context
    if ctx.oauth_metadata is None or not ctx.can_refresh_token():
        return False
    request = await provider._refresh_token()
    if client is None:
        async with httpx2.AsyncClient() as c:
            response = await c.send(request)
            await response.aread()
    else:
        response = await client.send(request)
        await response.aread()
    if await provider._handle_refresh_response(response):
        return True
    # As the SDK does after its own failed refresh: reload storage on the next request, so fresh
    # tokens from a re-login are used directly.
    provider._initialized = False
    return False


def is_auth_required(exc: BaseException) -> bool:
    if isinstance(exc, MonarchAuthRequired):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(is_auth_required(e) for e in exc.exceptions)
    return False


async def with_refresh_retry[T](
    provider: OAuthClientProvider,
    op: Callable[[], Awaitable[T]],
    *,
    client: httpx2.AsyncClient | None = None,
) -> T:
    """Run `op`; if it fails with MonarchAuthRequired, force one refresh and retry once.

    `op` must be re-runnable (e.g. open a fresh MCP session each call) and use `provider`.
    """
    try:
        return await op()
    except BaseException as exc:
        if not is_auth_required(exc):
            raise
        if not await force_refresh(provider, client=client):
            raise MonarchAuthRequired("Monarch login expired") from exc
    return await op()
