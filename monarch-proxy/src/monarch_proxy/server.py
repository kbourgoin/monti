"""Read-only MCP proxy server: allowlist filtering, call logging, auth-marker mapping."""

import logging
import os
import time
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from turn_contract import AUTH_ERROR_MARKER

from monarch_proxy.allowlist import is_allowed
from monarch_proxy.auth import MonarchAuthRequired
from monarch_proxy.calllog import CallLog
from monarch_proxy.upstream import MonarchUpstream, Upstream

log = logging.getLogger("monarch_proxy")

DEFAULT_CALL_LOG = Path("data/logs/monarch-calls.jsonl")


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def build_server(upstream: Upstream, call_log: CallLog) -> Server:
    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        tools = [t for t in await upstream.list_tools() if is_allowed(t.name)]
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        name, args = params.name, params.arguments or {}
        start = time.monotonic()

        def finish(result: types.CallToolResult) -> types.CallToolResult:
            size = len(result.model_dump_json(by_alias=True, exclude_none=True))
            ms = int((time.monotonic() - start) * 1000)
            call_log.record(tool=name, args=args, ms=ms, ok=not result.is_error, result_bytes=size)
            return result

        if not is_allowed(name):
            return finish(_error(f"Tool {name} is not available (read-only proxy)."))
        try:
            return finish(await upstream.call_tool(name, args))
        except MonarchAuthRequired:
            return finish(_error(f"{AUTH_ERROR_MARKER}: run make monarch-login"))
        except Exception:
            log.exception("upstream call failed: %s", name)
            return finish(_error(f"Upstream error calling {name}."))

    return Server("monarch-proxy", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def serve() -> None:
    import uvicorn

    from monarch_proxy.login import DEFAULT_TOKEN_PATH
    from monarch_proxy.tokens import FileTokenStorage

    tokens = Path(os.environ.get("MONARCH_TOKENS", DEFAULT_TOKEN_PATH))
    call_log = CallLog(Path(os.environ.get("MONARCH_CALL_LOG", DEFAULT_CALL_LOG)))
    host = os.environ.get("PROXY_HOST", "127.0.0.1")
    async with MonarchUpstream(FileTokenStorage(tokens)) as upstream:
        app = build_server(upstream, call_log).streamable_http_app(host=host)
        await uvicorn.Server(uvicorn.Config(app, host=host, port=8100)).serve()


def main() -> None:
    import asyncio

    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())
