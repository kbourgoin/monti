import pytest
from agent_deep import agent as agent_mod
from agent_deep.extract import auth_failed
from langchain_core.messages import ToolMessage
from mcp import types


class FakeClient:
    list_error: Exception | None = None
    result = types.CallToolResult(content=[types.TextContent(type="text", text="[accounts]")])

    def __init__(self, url):
        self.url = url

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def list_tools(self):
        if self.list_error:
            raise self.list_error
        tool = types.Tool(
            name="GetAccounts", description="d", input_schema={"type": "object", "properties": {}}
        )
        return types.ListToolsResult(tools=[tool])

    async def call_tool(self, name, arguments):
        return self.result


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    monkeypatch.setattr(agent_mod, "Client", FakeClient)
    FakeClient.list_error = None
    yield FakeClient


async def test_loads_tools_and_calls():
    (tool,) = await agent_mod.load_mcp_tools("http://x/mcp")
    assert tool.name == "GetAccounts"
    assert await tool.ainvoke({}) == "[accounts]"


async def test_list_failure_propagates(fake_client):
    fake_client.list_error = RuntimeError("MONARCH_AUTH_REQUIRED: login")
    with pytest.raises(RuntimeError, match="MONARCH_AUTH_REQUIRED"):
        await agent_mod.load_mcp_tools("http://x/mcp")


async def test_is_error_result_reaches_model_as_text_and_is_detected(fake_client):
    fake_client.result = types.CallToolResult(
        content=[types.TextContent(type="text", text="MONARCH_AUTH_REQUIRED: login")], is_error=True
    )
    (tool,) = await agent_mod.load_mcp_tools("http://x/mcp")
    out = await tool.ainvoke({})
    assert "MONARCH_AUTH_REQUIRED" in out
    assert auth_failed([ToolMessage(content=out, tool_call_id="1")])


async def test_build_agent_passes_no_backend(monkeypatch):
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)
        return "graph"

    monkeypatch.setattr(agent_mod, "create_deep_agent", fake_create)
    monkeypatch.setattr(agent_mod, "init_chat_model", lambda name: f"model:{name}")
    graph, _timer = await agent_mod.build_agent(
        mcp_url="http://x/mcp", model="m", checkpointer="cp"
    )
    assert graph == "graph"
    # No backend kwarg: deepagents falls back to its in-memory StateBackend, never the host disk.
    assert "backend" not in seen
    assert seen["model"] == "model:anthropic:m"
    assert seen["checkpointer"] == "cp"
    assert [t.name for t in seen["tools"]] == ["GetAccounts"]
    assert "Monti" in seen["system_prompt"]
