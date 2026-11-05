"""MonarchUpstream against a local fake Monarch MCP server whose token is revoked mid-session."""

import asyncio
import contextlib
import json
import socket
from urllib.parse import parse_qs

import pytest
import uvicorn
from mcp import Client, types
from mcp.server.lowlevel import Server
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from monarch_proxy.auth import MonarchAuthRequired
from monarch_proxy.calllog import CallLog
from monarch_proxy.server import build_server
from monarch_proxy.tokens import FileTokenStorage
from monarch_proxy.upstream import MonarchUpstream
from starlette.responses import JSONResponse, Response
from turn_contract import AUTH_ERROR_MARKER


class FakeMonarch:
    """ASGI app: a real MCP server at /mcp behind bearer checks, plus AS metadata and /token."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.valid_tokens = {"t1"}
        self.refresh_ok = True
        self.refreshes = 0

        async def on_list_tools(ctx, params) -> types.ListToolsResult:
            schema = {"type": "object", "properties": {}}
            return types.ListToolsResult(
                tools=[types.Tool(name="GetAccounts", description="d", input_schema=schema)]
            )

        async def on_call_tool(ctx, params) -> types.CallToolResult:
            return types.CallToolResult(content=[types.TextContent(type="text", text="ACCOUNTS")])

        server = Server("fake-monarch", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
        self.inner = server.streamable_http_app()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return await self.inner(scope, receive, send)
        path = scope["path"]
        if path == "/.well-known/oauth-authorization-server":
            meta = {
                "issuer": self.base,
                "authorization_endpoint": f"{self.base}/authorize",
                "token_endpoint": f"{self.base}/token",
            }
            return await JSONResponse(meta)(scope, receive, send)
        if path == "/token":
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body"):
                    break
            form = parse_qs(body.decode())
            assert form["grant_type"] == ["refresh_token"]
            self.refreshes += 1
            if not self.refresh_ok:
                return await JSONResponse({"error": "invalid_grant"}, 400)(scope, receive, send)
            token = f"t{self.refreshes + 1}"
            self.valid_tokens.add(token)
            resp = {"access_token": token, "token_type": "Bearer", "expires_in": 3600}
            return await JSONResponse(resp)(scope, receive, send)
        if path.startswith("/mcp"):
            headers = dict(scope["headers"])
            auth = headers.get(b"authorization", b"").decode()
            if auth.removeprefix("Bearer ") not in self.valid_tokens:
                return await Response(
                    status_code=401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'}
                )(scope, receive, send)
        return await self.inner(scope, receive, send)


@contextlib.asynccontextmanager
async def running_fake():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    fake = FakeMonarch(f"http://127.0.0.1:{port}")
    server = uvicorn.Server(uvicorn.Config(fake, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        yield fake
    finally:
        server.should_exit = True
        await task


async def _seeded_storage(tmp_path) -> FileTokenStorage:
    storage = FileTokenStorage(tmp_path / "tokens.json")
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="cid",
            redirect_uris=["http://127.0.0.1:8765/callback"],
            token_endpoint_auth_method="none",
        )
    )
    await storage.set_tokens(
        OAuthToken(access_token="t1", token_type="Bearer", refresh_token="r1", expires_in=3600)
    )
    return storage


async def test_mid_session_401_refreshes_reconnects_and_retries(tmp_path):
    async with running_fake() as fake:
        storage = await _seeded_storage(tmp_path)
        async with MonarchUpstream(storage, url=f"{fake.base}/mcp") as up:
            assert [t.name for t in await up.list_tools()] == ["GetAccounts"]
            fake.valid_tokens.clear()  # access token revoked while the session is open
            result = await asyncio.wait_for(up.call_tool("GetAccounts", {}), 10)
            assert result.content[0].text == "ACCOUNTS"
        assert fake.refreshes == 1
        assert (await storage.get_tokens()).access_token == "t2"


async def test_mid_session_401_with_dead_refresh_surfaces_marker(tmp_path):
    async with running_fake() as fake:
        storage = await _seeded_storage(tmp_path)
        async with MonarchUpstream(storage, url=f"{fake.base}/mcp") as up:
            await up.list_tools()
            fake.valid_tokens.clear()
            fake.refresh_ok = False
            with pytest.raises(MonarchAuthRequired):
                await asyncio.wait_for(up.call_tool("GetAccounts", {}), 10)
            # Through the proxy server the owner-facing result carries the auth marker.
            server = build_server(up, CallLog(tmp_path / "calls.jsonl"))
            async with Client(server) as client:
                r = await asyncio.wait_for(client.call_tool("GetAccounts", {}), 10)
            assert r.is_error and AUTH_ERROR_MARKER in r.content[0].text
            line = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
            assert line["ok"] is False
