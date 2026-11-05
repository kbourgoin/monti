"""deepagents agent behind the turn contract."""

import asyncio
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from turn_contract import (
    ERROR_STATUS,
    ErrorBody,
    ErrorResponse,
    Health,
    TurnRequest,
    TurnResponse,
    format_user_message,
    install_error_handlers,
)

from agent_deep.agent import build_agent
from agent_deep.extract import auth_failed, mentions_marker, reply_text, usage_from

TURN_TIMEOUT_S = 110
MODEL = os.environ.get("MODEL") or "claude-sonnet-5-5"

_checkpointer: AsyncSqliteSaver | None = None
_graph = None
_timer = None
_build_lock: asyncio.Lock | None = None
_session_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _data_dir() -> Path:
    default = Path(__file__).resolve().parents[4] / "data" / "agents" / "deep"
    return Path(os.environ.get("DEEP_DATA_DIR") or default)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Open the checkpointer only. Tools load lazily on the first turn (see _get_graph)."""
    global _checkpointer, _graph, _timer, _build_lock
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    _graph = _timer = None
    _build_lock = asyncio.Lock()
    _session_locks.clear()
    async with AsyncSqliteSaver.from_conn_string(str(d / "checkpoints.db")) as saver:
        _checkpointer = saver
        yield
    _checkpointer = None


app = FastAPI(lifespan=lifespan)
install_error_handlers(app)


def _error(code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(status_code=ERROR_STATUS[code], content=body.model_dump())


async def _get_graph():
    """Build once; a failed build is not cached, so the next turn retries it."""
    global _graph, _timer
    async with _build_lock:
        if _graph is None:
            mcp_url = os.environ.get("MONARCH_MCP_URL") or "http://monarch-proxy:8100/mcp"
            _graph, _timer = await build_agent(
                mcp_url=mcp_url, model=MODEL, checkpointer=_checkpointer
            )
        return _graph, _timer


@app.get("/health")
def health() -> Health:
    return Health(name="deep", framework="deepagents", model=MODEL)


@app.post("/turn")
async def turn(req: TurnRequest):
    try:
        graph, timer = await _get_graph()
    except Exception as exc:  # noqa: BLE001 - any failure maps to a contract error
        if mentions_marker(exc):
            return _error("monarch_auth", "Monarch login required (MONARCH_AUTH_REQUIRED)")
        return _error("internal", f"could not load tools: {exc!r}")

    config = {"configurable": {"thread_id": req.session_id}}
    async with _session_locks[req.session_id]:
        try:
            state = await graph.aget_state(config)
            before = len(state.values.get("messages", []))
            timer.start()
            result = await asyncio.wait_for(
                graph.ainvoke(
                    {"messages": [HumanMessage(format_user_message(req.text, req.now, req.sender_name))]},
                    config=config,
                ),
                TURN_TIMEOUT_S,
            )
        except TimeoutError:
            return _error("timeout", f"turn took longer than {TURN_TIMEOUT_S}s")
        except Exception as exc:  # noqa: BLE001 - any failure maps to a contract error
            if mentions_marker(exc):
                return _error("monarch_auth", "Monarch login required (MONARCH_AUTH_REQUIRED)")
            return _error("internal", f"{type(exc).__name__}: {exc}")
        new = result["messages"][before:]
        if auth_failed(new):
            return _error("monarch_auth", "Monarch login required (MONARCH_AUTH_REQUIRED)")
        return TurnResponse(
            reply=reply_text(new), tool_calls=list(timer.calls), usage=usage_from(new)
        )


def main() -> None:
    uvicorn.run(app, host=os.environ.get("AGENT_HOST", "127.0.0.1"), port=8203)
