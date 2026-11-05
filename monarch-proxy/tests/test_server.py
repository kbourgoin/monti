import json

import pytest
from mcp import Client, types
from mcp.shared.exceptions import MCPError
from monarch_proxy.auth import MonarchAuthRequired
from monarch_proxy.calllog import CallLog
from monarch_proxy.server import build_server
from turn_contract import AUTH_ERROR_MARKER


class FakeUpstream:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.raise_auth = False

    async def list_tools(self):
        schema = {"type": "object", "properties": {}}
        return [
            types.Tool(name=n, description=n, input_schema=schema)
            for n in ("GetAccounts", "GetTransactions", "UpdateTransaction")
        ]

    async def call_tool(self, name, args):
        if self.raise_auth:
            raise MonarchAuthRequired("expired")
        self.calls.append((name, args))
        return types.CallToolResult(content=[types.TextContent(type="text", text="SECRET-BODY")])


@pytest.fixture
def fake():
    return FakeUpstream()


def connect(fake, tmp_path):
    # A context manager rather than an async fixture: anyio scopes must exit in their entering task.
    return Client(build_server(fake, CallLog(tmp_path / "calls.jsonl")))


async def test_list_hides_write_tools(fake, tmp_path):
    async with connect(fake, tmp_path) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {"GetAccounts", "GetTransactions"}


async def test_write_call_refused_and_not_forwarded(fake, tmp_path):
    async with connect(fake, tmp_path) as client:
        r = await client.call_tool("UpdateTransaction", {})
    assert r.is_error and fake.calls == []
    assert r.content[0].text == "Tool UpdateTransaction is not available (read-only proxy)."


async def test_read_call_forwarded_and_logged(fake, tmp_path):
    async with connect(fake, tmp_path) as client:
        r = await client.call_tool("GetAccounts", {"intent": "budgeting"})
    assert not r.is_error and fake.calls == [("GetAccounts", {"intent": "budgeting"})]
    assert r.content[0].text == "SECRET-BODY"
    text = (tmp_path / "calls.jsonl").read_text()
    line = json.loads(text.splitlines()[-1])
    assert line["tool"] == "GetAccounts" and line["ok"] is True and "result" not in line
    assert set(line) == {"ts", "tool", "args", "ms", "ok", "result_bytes"}
    assert line["result_bytes"] > 0 and "SECRET-BODY" not in text


async def test_auth_failure_returns_marker(fake, tmp_path):
    fake.raise_auth = True
    async with connect(fake, tmp_path) as client:
        r = await client.call_tool("GetAccounts", {})
    assert r.is_error and AUTH_ERROR_MARKER in r.content[0].text
    assert r.content[0].text == "MONARCH_AUTH_REQUIRED: run make monarch-login"
    line = json.loads((tmp_path / "calls.jsonl").read_text().splitlines()[-1])
    assert line["ok"] is False


async def test_list_auth_failure_error_contains_marker(fake, tmp_path):
    fake.raise_auth = True

    async def list_tools():
        raise MonarchAuthRequired("expired")

    fake.list_tools = list_tools
    async with connect(fake, tmp_path) as client:
        with pytest.raises(MCPError) as exc:
            await client.list_tools()
    assert AUTH_ERROR_MARKER in str(exc.value)
