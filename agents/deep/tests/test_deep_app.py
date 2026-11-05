import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from agent_deep import app as app_mod
from agent_deep.agent import ToolTimer
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from turn_contract import ToolCall


class FakeGraph:
    def __init__(self, behavior):
        self.behavior = behavior
        self.history: dict[str, list] = {}

    async def aget_state(self, config):
        msgs = list(self.history.get(config["configurable"]["thread_id"], []))
        return SimpleNamespace(values={"messages": msgs})

    async def ainvoke(self, inp, config):
        tid = config["configurable"]["thread_id"]
        msgs = self.history.setdefault(tid, [])
        msgs.extend(inp["messages"])
        await self.behavior(msgs, self.timer)
        return {"messages": list(msgs)}


async def _answer(msgs, timer):
    timer.calls.append(ToolCall(name="GetAccounts", args={}, ms=5, ok=True))
    msgs.append(
        AIMessage(
            content="hello back",
            usage_metadata={"input_tokens": 7, "output_tokens": 2, "total_tokens": 9},
        )
    )


def _body(text="hi"):
    return {
        "session_id": "s1",
        "message_id": "m1",
        "text": text,
        "now": datetime.now(UTC).isoformat(),
    }


@pytest.fixture
def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEP_DATA_DIR", str(tmp_path))
    builds = []

    def install(behavior=_answer, build_error=None):
        async def fake_build(*, mcp_url, model, checkpointer):
            builds.append(1)
            if build_error and len(builds) == 1:
                raise build_error
            g = FakeGraph(behavior)
            t = ToolTimer()
            g.timer = t
            return g, t

        monkeypatch.setattr(app_mod, "build_agent", fake_build)
        return TestClient(app_mod.app)

    install.builds = builds
    return install


def test_turn_ok(make_client):
    with make_client() as c:
        r = c.post("/turn", json=_body())
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["reply"] == "hello back"
        assert j["usage"] == {"input_tokens": 7, "output_tokens": 2}
        assert j["tool_calls"][0]["name"] == "GetAccounts"

        # second turn: usage counts only the new messages, tool calls reset
        async def quiet(msgs, timer):
            msgs.append(
                AIMessage(
                    content="again",
                    usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                )
            )

        app_mod._graph.behavior = quiet
        j2 = c.post("/turn", json=_body()).json()
        assert j2["usage"] == {"input_tokens": 1, "output_tokens": 1}
        assert j2["tool_calls"] == []


def test_health(make_client):
    with make_client() as c:
        j = c.get("/health").json()
        assert j["name"] == "deep" and j["framework"] == "deepagents" and j["model"]


def test_malformed_400(make_client):
    with make_client() as c:
        assert c.post("/turn", json={}).status_code == 400


def test_turn_timeout_maps_504(make_client, monkeypatch):
    async def slow(msgs, timer):
        await asyncio.sleep(5)

    monkeypatch.setattr(app_mod, "TURN_TIMEOUT_S", 0.01)
    with make_client(slow) as c:
        r = c.post("/turn", json=_body())
        assert r.status_code == 504
        assert r.json()["error"]["code"] == "timeout"


def test_tool_error_message_maps_502(make_client):
    async def authfail(msgs, timer):
        msgs.append(
            ToolMessage(content="MONARCH_AUTH_REQUIRED: login", tool_call_id="1", status="error")
        )
        msgs.append(AIMessage(content="sorry"))

    with make_client(authfail) as c:
        r = c.post("/turn", json=_body())
        assert r.status_code == 502 and r.json()["error"]["code"] == "monarch_auth"


def test_tool_exception_maps_502(make_client):
    async def raises(msgs, timer):
        raise ExceptionGroup("tg", [RuntimeError("MONARCH_AUTH_REQUIRED: login")])

    with make_client(raises) as c:
        r = c.post("/turn", json=_body())
        assert r.status_code == 502 and r.json()["error"]["code"] == "monarch_auth"


def test_other_exception_maps_500(make_client):
    async def raises(msgs, timer):
        raise RuntimeError("boom")

    with make_client(raises) as c:
        r = c.post("/turn", json=_body())
        assert r.status_code == 500 and r.json()["error"]["code"] == "internal"


def test_tools_list_auth_failure_is_502_then_recovers(make_client):
    err = ExceptionGroup("unhandled", [RuntimeError("MONARCH_AUTH_REQUIRED: login")])
    with make_client(build_error=err) as c:
        r = c.post("/turn", json=_body())
        assert r.status_code == 502 and r.json()["error"]["code"] == "monarch_auth"
        # startup did not fail, and the next turn retries the build
        assert c.get("/health").status_code == 200
        r2 = c.post("/turn", json=_body())
        assert r2.status_code == 200, r2.text
        assert len(make_client.builds) == 2


def test_tools_list_other_failure_is_500(make_client):
    with make_client(build_error=RuntimeError("connection refused")) as c:
        r = c.post("/turn", json=_body())
        assert r.status_code == 500 and r.json()["error"]["code"] == "internal"


def test_no_tool_loading_at_startup(make_client):
    with make_client():
        assert make_client.builds == []
