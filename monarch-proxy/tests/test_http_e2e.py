import asyncio
import socket

import uvicorn
from mcp import Client
from monarch_proxy.calllog import CallLog
from monarch_proxy.server import build_server
from test_server import FakeUpstream


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
    finally:
        server.should_exit = True
        await task
