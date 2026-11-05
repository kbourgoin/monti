from agent_deep.extract import auth_failed, mentions_marker, reply_text, usage_from
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def _ai(i: int, o: int) -> AIMessage:
    return AIMessage(
        content="x", usage_metadata={"input_tokens": i, "output_tokens": o, "total_tokens": i + o}
    )


def test_usage_sums_ai_messages_only():
    msgs = [
        HumanMessage(content="hi"),
        _ai(10, 3),
        ToolMessage(content="r", tool_call_id="1"),
        _ai(20, 4),
    ]
    u = usage_from(msgs)
    assert (u.input_tokens, u.output_tokens) == (30, 7)


def test_usage_tolerates_missing_metadata():
    assert usage_from([AIMessage(content="x")]).input_tokens == 0


def test_auth_failed_detects_marker():
    msgs = [ToolMessage(content="MONARCH_AUTH_REQUIRED: run login", tool_call_id="1")]
    assert auth_failed(msgs) is True


def test_auth_failed_detects_marker_in_content_blocks_and_error_status():
    blocks = [{"type": "text", "text": "MONARCH_AUTH_REQUIRED: x"}]
    assert auth_failed([ToolMessage(content=blocks, tool_call_id="1", status="error")])


def test_auth_failed_false_for_normal_messages():
    assert not auth_failed(
        [ToolMessage(content="ok", tool_call_id="1"), AIMessage(content="MONARCH_AUTH_REQUIRED")]
    )


def test_mentions_marker_walks_exception_groups_and_causes():
    inner = RuntimeError("MONARCH_AUTH_REQUIRED: nope")
    assert mentions_marker(ExceptionGroup("unhandled errors in a TaskGroup", [inner]))
    try:
        try:
            raise inner
        except RuntimeError as e:
            raise ValueError("wrapped") from e
    except ValueError as w:
        assert mentions_marker(w)
    assert not mentions_marker(RuntimeError("boom"))


def test_reply_text_joins_text_blocks():
    m = AIMessage(
        content=[
            {"type": "text", "text": "a"},
            {"type": "tool_use", "id": "1", "name": "t", "input": {}},
            {"type": "text", "text": "b"},
        ]
    )
    assert reply_text([HumanMessage(content="q"), m]) == "ab"
