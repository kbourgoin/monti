import sqlite3

from gateway.store import Store, TurnRecord


def test_session_stable_then_rotates(tmp_path):
    s = Store(tmp_path / "g.db")
    a = s.current_session("o", "pi")
    assert s.current_session("o", "pi") == a
    b = s.rotate_session("o", "pi")
    assert b != a and s.current_session("o", "pi") == b


def test_sessions_per_agent(tmp_path):
    s = Store(tmp_path / "g.db")
    assert s.current_session("o", "pi") != s.current_session("o", "deep")


def test_persists_across_instances(tmp_path):
    p = tmp_path / "g.db"
    assert Store(p).active_agent("o") is None
    Store(p).set_active_agent("o", "deep")
    assert Store(p).active_agent("o") == "deep"


def test_log_turn(tmp_path):
    p = tmp_path / "g.db"
    Store(p).log_turn(
        TurnRecord("s", "pi", "hi", "yo", "t0", "t1", "ok", [{"name": "x"}], 3, 4)
    )
    rows = sqlite3.connect(p).execute("select session_id, status, input_tokens from turns").fetchall()
    assert rows == [("s", "ok", 3)]
