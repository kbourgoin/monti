"""Pull usage, reply text and auth failures out of a turn's messages."""

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from turn_contract import AUTH_ERROR_MARKER, Usage


def usage_from(messages: list[BaseMessage]) -> Usage:
    """Sum token usage over the AI messages in `messages` (pass only this turn's)."""
    total = Usage()
    for m in messages:
        if isinstance(m, AIMessage) and m.usage_metadata:
            total.input_tokens += m.usage_metadata.get("input_tokens", 0)
            total.output_tokens += m.usage_metadata.get("output_tokens", 0)
    return total


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b if isinstance(b, str) else b.get("text", "")
            for b in content
            if isinstance(b, (str, dict))
        )
    return str(content)


def auth_failed(messages: list[BaseMessage]) -> bool:
    """True if any tool message (error or not) carries the proxy's auth marker."""
    return any(
        isinstance(m, ToolMessage) and AUTH_ERROR_MARKER in _text(m.content) for m in messages
    )


def mentions_marker(exc: BaseException | None, _seen: set[int] | None = None) -> bool:
    """True if the exception, its group members, cause or context mention the auth marker."""
    seen = _seen if _seen is not None else set()
    if exc is None or id(exc) in seen:
        return False
    seen.add(id(exc))
    if AUTH_ERROR_MARKER in str(exc):
        return True
    children = list(getattr(exc, "exceptions", ()) or ())
    children += [exc.__cause__, exc.__context__]
    return any(mentions_marker(c, seen) for c in children)


def reply_text(messages: list[BaseMessage]) -> str:
    """Text of the last AI message."""
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return _text(m.content).strip()
    return ""
