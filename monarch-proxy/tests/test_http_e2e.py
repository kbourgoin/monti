import asyncio
import socket

import httpx
import pytest
import uvicorn
from mcp import Client
from mcp.shared.exceptions import MCPError
from monarch_proxy.auth import MonarchAuthRequired
from monarch_proxy.calllog import CallLog
from monarch_proxy.server import build_app, build_server
from test_server import FakeUpstream
from turn_contract import AUTH_ERROR_MARKER


async def _raise_auth():
    raise MonarchAuthRequired("expired")


async def test_streamable_http_end_to_end(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    fake = FakeUpstream()
    app = build_server(fake, CallLog(tmp_path / "calls.jsonl")).streamable_http_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            names = {t.name for t in (await client.list_tools()).tools}
            ok = await client.call_tool("GetAccounts", {"intent": "budgeting"})
            refused = await client.call_tool("UpdateTransaction", {})
        assert names == {"GetAccounts", "GetTransactions"}
        assert not ok.is_error and fake.calls == [("GetAccounts", {"intent": "budgeting"})]
        assert refused.is_error
        fake.list_tools = _raise_auth
        async with Client(f"http://127.0.0.1:{port}/mcp") as client:
            with pytest.raises(MCPError) as exc:
                await client.list_tools()
        assert AUTH_ERROR_MARKER in str(exc.value)
    finally:
        server.should_exit = True
        await task


_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "0"},
    },
}


@pytest.mark.parametrize("bind", ["0.0.0.0", "127.0.0.1"])
async def test_dns_rebinding_rejected_whatever_the_bind_host(tmp_path, bind):
    # serve() binds 0.0.0.0 under compose; the published port must still refuse foreign
    # Host/Origin headers (a DNS-rebinding page would otherwise read every tool).
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = build_app(build_server(FakeUpstream(), CallLog(tmp_path / "c.jsonl")), host=bind)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    headers = {"Accept": "application/json, text/event-stream"}

    async def post(**extra):
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"http://127.0.0.1:{port}/mcp", json=_INIT, headers={**headers, **extra}
            )
            return r.status_code

    try:
        while not server.started:
            await asyncio.sleep(0.01)
        assert await post(Host="evil.example:8100") == 421
        assert await post(Host="evil.example") == 421
        assert await post(Origin="http://evil.example") == 403
        assert await post(Host="monarch-proxy:8100") == 200
        assert await post(Host=f"127.0.0.1:{port}") == 200
        assert await post(Host=f"localhost:{port}", Origin=f"http://localhost:{port}") == 200
    finally:
        server.should_exit = True
        await task
