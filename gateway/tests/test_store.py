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
        TurnRecord("dm:+1", "Keith", "s", "pi", "hi", "yo", "t0", "t1", "ok", [{"name": "x"}], 3, 4)
    )
    rows = sqlite3.connect(p).execute("select session_id, status, input_tokens from turns").fetchall()
    assert rows == [("s", "ok", 3)]


def test_migrates_v1_owner_schema(tmp_path):
    p = tmp_path / "g.db"
    c = sqlite3.connect(p)
    c.executescript(
        "create table active_agent (owner text primary key, agent text not null);"
        "create table sessions (owner text not null, agent text not null, session_id text not null,"
        " primary key (owner, agent));"
        "create table turns (id integer primary key autoincrement, session_id text not null,"
        " agent text not null, inbound text not null, reply text, started_at text not null,"
        " finished_at text not null, status text not null, tool_calls text not null,"
        " input_tokens integer not null, output_tokens integer not null);"
        "insert into active_agent values('+1', 'deep');"
        "insert into sessions values('+1', 'deep', 'sid');"
    )
    c.commit()
    c.close()
    s = Store(p)
    assert s.active_agent("dm:+1") == "deep" and s.current_session("dm:+1", "deep") == "sid"
    s.log_turn(TurnRecord("dm:+1", "Keith", "sid", "deep", "hi", "yo", "t0", "t1", "ok", [], 1, 2))
