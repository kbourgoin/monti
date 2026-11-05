from datetime import UTC, datetime
from uuid import uuid4

from agent_echo.app import app
from fastapi.testclient import TestClient

client = TestClient(app)


def turn(sid: str, text: str):
    return client.post(
        "/turn",
        json={
            "session_id": sid,
            "message_id": str(uuid4()),
            "text": text,
            "now": datetime.now(UTC).isoformat(),
        },
    )


def test_turn_counter_per_session():
    sid = str(uuid4())
    assert turn(sid, "a").json()["reply"] == "echo: a (turn 1)"
    assert turn(sid, "b").json()["reply"] == "echo: b (turn 2)"
    assert turn(str(uuid4()), "c").json()["reply"] == "echo: c (turn 1)"


def test_health():
    assert client.get("/health").json() == {"name": "echo", "framework": "echo", "model": "none"}
