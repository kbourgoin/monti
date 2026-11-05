"""Agent contract suite. Run with AGENT_URL=http://host:port; add -m llm for LLM tests."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from turn_contract import ErrorResponse, Health, TurnResponse


@pytest.fixture
def post_turn(client):
    def _post(text: str, session_id=None):
        body = {
            "session_id": str(session_id or uuid4()),
            "message_id": str(uuid4()),
            "text": text,
            "now": datetime.now(UTC).isoformat(),
        }
        return client.post("/turn", json=body)

    return _post


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    Health.model_validate(r.json())


def test_basic_turn(post_turn):
    r = post_turn("hello")
    assert r.status_code == 200
    assert TurnResponse.model_validate(r.json()).reply.strip()


def test_malformed_request(client):
    r = client.post("/turn", json={})
    assert r.status_code == 400
    ErrorResponse.model_validate(r.json())


@pytest.mark.llm
def test_memory_within_session(post_turn):
    sid = uuid4()
    seed = post_turn("Remember the code word teal-42.", sid)
    assert seed.status_code == 200, seed.text
    assert "teal-42" in post_turn("What was the code word?", sid).json()["reply"]


@pytest.mark.llm
def test_sessions_isolated(post_turn):
    seed = post_turn("Remember the code word teal-42.", uuid4())
    assert seed.status_code == 200, seed.text  # else isolation would pass vacuously
    reply = post_turn("What was the code word?", uuid4()).json()["reply"]
    assert "teal-42" not in reply


@pytest.mark.llm
def test_uses_monarch_tool(post_turn):
    r = post_turn("What accounts do I have?").json()
    assert "Webster" in r["reply"]
    assert any(c["name"] == "GetAccounts" for c in r["tool_calls"])


@pytest.mark.llm
def test_monarch_auth_error(post_turn, set_fake_mode):
    set_fake_mode("auth_error")
    try:
        r = post_turn("What accounts do I have?")
        assert r.status_code == 502
        assert r.json()["error"]["code"] == "monarch_auth"
    finally:
        set_fake_mode("ok")
