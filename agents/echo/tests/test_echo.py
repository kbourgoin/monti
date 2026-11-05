from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

from agent_echo.app import app
from fastapi.testclient import TestClient
from turn_contract import format_user_message

client = TestClient(app)


def turn(sid: str, text: str, sender: str = "Zed"):
    return client.post(
        "/turn",
        json={
            "session_id": sid,
            "message_id": str(uuid4()),
            "text": text,
            "sender_name": sender,
            "now": datetime.now(UTC).isoformat(),
        },
    )


def test_turn_counter_per_session():
    sid = str(uuid4())
    assert turn(sid, "a").json()["reply"] == "echo (Zed): a (turn 1)"
    assert turn(sid, "b").json()["reply"] == "echo (Zed): b (turn 2)"
    assert turn(str(uuid4()), "c", "Amy").json()["reply"] == "echo (Amy): c (turn 1)"


def test_health():
    assert client.get("/health").json() == {"name": "echo", "framework": "echo", "model": "none"}


def test_format_user_message_includes_from():
    now = datetime(2026, 11, 4, 22, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert (
        format_user_message("hi", now, "Zed")
        == "Current time: 2026-11-04T22:00:00-05:00\nFrom: Zed\n\nhi"
    )
