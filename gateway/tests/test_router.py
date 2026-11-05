import sqlite3
from datetime import datetime

import pytest
from gateway.agent_client import AgentDown, AgentError, AgentTimeout
from gateway.config import Settings
from gateway.router import Inbound, Router, is_owner
from gateway.store import Store
from turn_contract import Health, ToolCall, TurnResponse, Usage

OWNER = "+15551112222"


class FakeAgents:
    def __init__(self):
        self.calls = []
        self.exc = None
        self.reply = TurnResponse(reply="ok")
        self.health_exc = None

    async def turn(self, agent, req):
        self.calls.append((agent, req))
        if self.exc:
            raise self.exc
        return self.reply

    async def health(self, agent):
        if self.health_exc:
            raise self.health_exc
        return Health(name=agent, framework="fw", model="model-x")


@pytest.fixture
def settings(tmp_path):
    return Settings(
        owner_number=OWNER,
        owner_uuid="uuid-owner",
        bot_number="+15553334444",
        signal_url="http://signal",
        agents={"pi": "http://pi", "deep": "http://deep", "echo": "http://echo"},
        default_agent="pi",
        db_path=tmp_path / "g.db",
    )


@pytest.fixture
def fake():
    return FakeAgents()


@pytest.fixture
def store(settings):
    return Store(settings.db_path)


@pytest.fixture
def r(settings, store, fake):
    return Router(settings, store, fake)


def inb(text="hi", number=OWNER, uuid=None, group=False):
    return Inbound(number, uuid, text, 1, group)


def rows(settings):
    c = sqlite3.connect(settings.db_path)
    return c.execute("select status, tool_calls, input_tokens, output_tokens from turns").fetchall()


async def test_drops_non_owner(r, fake):
    assert await r.handle(inb(number="+15550000000")) == [] and fake.calls == []


async def test_drops_group(r, fake):
    assert await r.handle(inb(group=True)) == [] and fake.calls == []


async def test_owner_match_by_uuid(r, fake, settings):
    assert await r.handle(inb(number=None, uuid="uuid-owner")) == ["ok"]
    assert await r.handle(inb(number=None, uuid="other")) == []
    assert is_owner(inb(number=None, uuid="uuid-owner"), settings)


async def test_routes_to_default_agent_with_session_and_now(r, fake, store):
    await r.handle(inb("what?"))
    agent, req = fake.calls[0]
    assert agent == "pi" and req.text == "what?"
    assert req.session_id == store.current_session(OWNER, "pi")
    assert req.now.tzinfo.key == "America/New_York" and req.now.microsecond == 0
    assert isinstance(req.now, datetime) and req.message_id


async def test_agent_command_switches(r, fake):
    out = await r.handle(inb("/agent deep"))
    assert len(out) == 1 and "deep" in out[0]
    await r.handle(inb("hello"))
    assert fake.calls[0][0] == "deep"


async def test_unknown_agent_lists_choices(r, fake):
    out = await r.handle(inb("/agent foo"))
    assert len(out) == 1
    assert "foo" in out[0] and "deep" in out[0] and "echo" in out[0] and "pi" in out[0]
    await r.handle(inb("hello"))
    assert fake.calls[0][0] == "pi"


async def test_new_rotates_only_current_agent(r, store):
    pi, deep = store.current_session(OWNER, "pi"), store.current_session(OWNER, "deep")
    out = await r.handle(inb("/new"))
    assert len(out) == 1
    assert store.current_session(OWNER, "pi") != pi
    assert store.current_session(OWNER, "deep") == deep


async def test_which_shows_agent_session_model(r, store):
    out = await r.handle(inb("/which"))
    assert "pi" in out[0] and store.current_session(OWNER, "pi") in out[0] and "model-x" in out[0]


async def test_which_survives_agent_down(r, store, fake):
    fake.health_exc = AgentDown("pi")
    out = await r.handle(inb("/which"))
    assert "pi" in out[0] and store.current_session(OWNER, "pi") in out[0]
    assert "unavailable" in out[0]


async def test_help_lists_commands(r):
    out = (await r.handle(inb("/help")))[0]
    for c in ("/agent", "/new", "/which", "/help"):
        assert c in out


async def test_unknown_slash_goes_to_agent(r, fake):
    await r.handle(inb("/spending"))
    assert fake.calls[0][1].text == "/spending"


async def test_timeout_copy(r, fake, settings):
    fake.exc = AgentTimeout()
    assert await r.handle(inb()) == ["That took too long. Try again or /new."]
    assert rows(settings)[0][0] == "timeout"


async def test_down_copy(r, fake, settings):
    fake.exc = AgentDown("pi")
    assert await r.handle(inb()) == ["The pi agent is down."]
    assert rows(settings)[0][0] == "error"


async def test_monarch_auth_copy(r, fake):
    fake.exc = AgentError("monarch_auth", "expired")
    assert await r.handle(inb()) == ["Monarch login expired. Run `mise run monarch-login` on the Mac."]


async def test_other_error_copy_and_logged(r, fake, settings):
    fake.exc = AgentError("internal", "boom")
    assert await r.handle(inb()) == ["The pi agent is down."]
    assert rows(settings)[0][0] == "error"


async def test_agent_error_timeout_code(r, fake):
    fake.exc = AgentError("timeout", "slow")
    assert await r.handle(inb()) == ["That took too long. Try again or /new."]


async def test_long_reply_split(r, fake):
    fake.reply = TurnResponse(reply=("a" * 1000 + "\n\n") * 3)
    assert len(await r.handle(inb())) >= 2


async def test_turn_logged(r, fake, settings):
    fake.reply = TurnResponse(
        reply="done",
        tool_calls=[ToolCall(name="GetAccounts", args={}, ms=5, ok=True)],
        usage=Usage(input_tokens=11, output_tokens=7),
    )
    await r.handle(inb())
    got = rows(settings)
    assert len(got) == 1
    assert got[0][0] == "ok" and "GetAccounts" in got[0][1] and got[0][2:] == (11, 7)


async def test_commands_not_logged(r, settings):
    await r.handle(inb("/new"))
    assert rows(settings) == []
