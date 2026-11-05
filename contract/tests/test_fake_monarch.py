import asyncio
import json
import socket

import httpx
import pytest
import uvicorn
from mcp import Client
from mcp.shared.exceptions import MCPError
from turn_contract import AUTH_ERROR_MARKER
from turn_contract.fake_monarch import build_app


async def test_fake_monarch_over_http():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    base = f"http://127.0.0.1:{port}"
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        async with Client(f"{base}/mcp") as client:
            names = {t.name for t in (await client.list_tools()).tools}
            result = await client.call_tool("GetAccounts", {})
        assert names == {"GetAccounts", "GetTransactions"}
        assert not result.is_error
        assert json.loads(result.content[0].text)["accounts"][0]["name"] == "Webster Checking"

        async with httpx.AsyncClient() as http:
            (await http.post(f"{base}/_mode", json={"mode": "auth_error"})).raise_for_status()
        async with Client(f"{base}/mcp") as client:
            with pytest.raises(MCPError) as exc:
                await client.list_tools()
            called = await client.call_tool("GetAccounts", {})
        assert AUTH_ERROR_MARKER in str(exc.value)
        assert called.is_error and AUTH_ERROR_MARKER in called.content[0].text
    finally:
        server.should_exit = True
        await task
