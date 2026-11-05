"""Fake Monarch MCP server (streamable HTTP) for agent contract tests.

Run: uv run fake-monarch --port 8190. Toggle failures: POST /_mode {"mode": "ok"|"auth_error"}.
"""

import argparse
import json
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from turn_contract import AUTH_ERROR_MARKER

ACCOUNTS = {"accounts": [{"name": "Webster Checking", "balance": 1234.56}]}
TRANSACTIONS = {
    "transactions": [
        {"date": "2026-10-30", "merchant": "Trader Joe's", "amount": -54.21},
        {"date": "2026-10-28", "merchant": "Payroll", "amount": 2500.00},
    ]
}
_SCHEMA = {"type": "object", "properties": {}}
TOOLS = [
    types.Tool(name="GetAccounts", description="List accounts.", input_schema=_SCHEMA),
    types.Tool(name="GetTransactions", description="List transactions.", input_schema=_SCHEMA),
]
AUTH_TEXT = f"{AUTH_ERROR_MARKER}: run make monarch-login"


def build_app() -> Starlette:
    state = {"mode": "ok"}

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        if state["mode"] == "auth_error":
            raise MCPError(types.INTERNAL_ERROR, AUTH_TEXT)
        return types.ListToolsResult(tools=TOOLS)

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        if state["mode"] == "auth_error":
            text, is_error = AUTH_TEXT, True
        elif params.name == "GetAccounts":
            text, is_error = json.dumps(ACCOUNTS), False
        elif params.name == "GetTransactions":
            text, is_error = json.dumps(TRANSACTIONS), False
        else:
            text, is_error = f"Unknown tool {params.name}", True
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=text)], is_error=is_error
        )

    async def set_mode(request: Request) -> JSONResponse:
        mode = (await request.json()).get("mode")
        if mode not in ("ok", "auth_error"):
            return JSONResponse({"error": "mode must be ok or auth_error"}, status_code=400)
        state["mode"] = mode
        return JSONResponse({"mode": mode})

    server = Server("fake-monarch", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
    app = server.streamable_http_app()
    app.router.routes.append(Route("/_mode", set_mode, methods=["POST"]))
    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8190)
    args = parser.parse_args()
    uvicorn.run(build_app(), host="127.0.0.1", port=args.port)
