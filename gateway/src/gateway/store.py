import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_SCHEMA = """
create table if not exists active_agent (owner text primary key, agent text not null);
create table if not exists sessions (
    owner text not null, agent text not null, session_id text not null,
    primary key (owner, agent));
create table if not exists turns (
    id integer primary key autoincrement,
    session_id text not null, agent text not null, inbound text not null, reply text,
    started_at text not null, finished_at text not null, status text not null,
    tool_calls text not null, input_tokens integer not null, output_tokens integer not null);
"""


@dataclass
class TurnRecord:
    session_id: str
    agent: str
    inbound: str
    reply: str | None
    started_at: str
    finished_at: str
    status: Literal["ok", "timeout", "error"]
    tool_calls: list[dict]
    input_tokens: int
    output_tokens: int


class Store:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def active_agent(self, owner: str) -> str | None:
        row = self._db.execute("select agent from active_agent where owner=?", (owner,)).fetchone()
        return row[0] if row else None

    def set_active_agent(self, owner: str, agent: str) -> None:
        self._db.execute(
            "insert into active_agent(owner, agent) values(?,?) "
            "on conflict(owner) do update set agent=excluded.agent",
            (owner, agent),
        )
        self._db.commit()

    def current_session(self, owner: str, agent: str) -> str:
        row = self._db.execute(
            "select session_id from sessions where owner=? and agent=?", (owner, agent)
        ).fetchone()
        return row[0] if row else self.rotate_session(owner, agent)

    def rotate_session(self, owner: str, agent: str) -> str:
        sid = str(uuid.uuid4())
        self._db.execute(
            "insert into sessions(owner, agent, session_id) values(?,?,?) "
            "on conflict(owner, agent) do update set session_id=excluded.session_id",
            (owner, agent, sid),
        )
        self._db.commit()
        return sid

    def log_turn(self, rec: TurnRecord) -> None:
        self._db.execute(
            "insert into turns(session_id, agent, inbound, reply, started_at, finished_at, "
            "status, tool_calls, input_tokens, output_tokens) values(?,?,?,?,?,?,?,?,?,?)",
            (
                rec.session_id, rec.agent, rec.inbound, rec.reply, rec.started_at,
                rec.finished_at, rec.status, json.dumps(rec.tool_calls),
                rec.input_tokens, rec.output_tokens,
            ),
        )
        self._db.commit()
