import pytest
from agent_deep.agent import ToolTimer, wrap_tool
from langchain_core.tools import StructuredTool


def _tool(fn, name="Echo"):
    return StructuredTool(
        name=name,
        description="d",
        args_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        coroutine=fn,
    )


async def test_tool_timer_records_calls():
    async def ok(**kw):
        return "fine"

    timer = ToolTimer()
    wrapped = wrap_tool(_tool(ok), timer)
    timer.start()
    assert await wrapped.ainvoke({"x": 1}) == "fine"
    (call,) = timer.calls
    assert (call.name, call.args, call.ok) == ("Echo", {"x": 1}, True)
    assert call.ms >= 0


async def test_tool_timer_records_failure():
    async def bad(**kw):
        raise RuntimeError("boom")

    timer = ToolTimer()
    wrapped = wrap_tool(_tool(bad, "Bad"), timer)
    timer.start()
    with pytest.raises(RuntimeError):
        await wrapped.ainvoke({"x": 2})
    assert [(c.name, c.ok) for c in timer.calls] == [("Bad", False)]


async def test_timer_start_resets_per_turn():
    async def ok(**kw):
        return "fine"

    timer = ToolTimer()
    wrapped = wrap_tool(_tool(ok), timer)
    timer.start()
    await wrapped.ainvoke({})
    timer.start()
    assert timer.calls == []
