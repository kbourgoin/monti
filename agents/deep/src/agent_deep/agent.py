"""Build the deepagents graph over the Monarch MCP tools, with per-turn tool timing."""

import os
import time
from contextvars import ContextVar
from pathlib import Path

from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from mcp import Client, types
from turn_contract import ToolCall

SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[4] / "prompts" / "system.md"


class ToolTimer:
    """Tool calls for the current turn, held in a contextvar (shared with child tasks)."""

    def __init__(self) -> None:
        self._calls: ContextVar[list[ToolCall]] = ContextVar("deep_tool_calls")

    def start(self) -> None:
        self._calls.set([])

    @property
    def calls(self) -> list[ToolCall]:
        return self._calls.get()  # LookupError if start() was never called


def wrap_tool(tool: BaseTool, timer: ToolTimer) -> BaseTool:
    """Same tool, but each call is recorded on `timer`. Errors are recorded then re-raised."""

    async def run(**kwargs):
        t0 = time.perf_counter()
        ok = False
        try:
            result = await tool.coroutine(**kwargs)
            ok = True
            return result
        finally:
            try:
                timer.calls.append(
                    ToolCall(
                        name=tool.name,
                        args={k: v for k, v in kwargs.items() if k != "runtime"},
                        ms=int((time.perf_counter() - t0) * 1000),
                        ok=ok,
                    )
                )
            except LookupError:
                pass

    return StructuredTool(
        name=tool.name,
        description=tool.description,
        args_schema=tool.args_schema,
        coroutine=run,
        response_format=tool.response_format,
        handle_tool_error=tool.handle_tool_error,
        metadata=tool.metadata,
    )


def _result_text(result: types.CallToolResult) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))


async def load_mcp_tools(mcp_url: str) -> list[BaseTool]:
    """Load the MCP server's tools as LangChain tools (one tools/list call).

    langchain-mcp-adapters 0.3.x imports mcp 1.x internals, but this workspace is on mcp 2
    (monarch-proxy and the contract package need it), so tools are adapted here directly.
    A tools/list failure propagates (its message may carry MONARCH_AUTH_REQUIRED); a tool
    call that returns isError is handed to the model as the tool's output text.
    """
    async with Client(mcp_url) as client:
        listed = await client.list_tools()

    def adapt(t: types.Tool) -> BaseTool:
        async def call(**arguments):
            async with Client(mcp_url) as client:
                result = await client.call_tool(t.name, arguments)
            text = _result_text(result)
            if result.is_error:
                raise ToolException(text or "MCP tool returned an error")
            return text

        return StructuredTool(
            name=t.name,
            description=t.description or "",
            args_schema=t.input_schema,
            coroutine=call,
            handle_tool_error=True,
        )

    return [adapt(t) for t in listed.tools]


def _system_prompt() -> str:
    return Path(os.environ.get("SYSTEM_PROMPT_PATH") or SYSTEM_PROMPT_PATH).read_text().strip()


async def build_agent(*, mcp_url: str, model: str, checkpointer):
    """Load the Monarch tools (a tools/list call) and compile the agent. No filesystem backend."""
    tools = await load_mcp_tools(mcp_url)
    timer = ToolTimer()
    graph = create_deep_agent(
        model=init_chat_model(f"anthropic:{model}"),
        tools=[wrap_tool(t, timer) for t in tools],
        system_prompt=_system_prompt(),
        checkpointer=checkpointer,
    )
    return graph, timer
