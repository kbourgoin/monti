import sqlite3
from datetime import datetime

import pytest
from gateway.agent_client import AgentDown, AgentError, AgentTimeout
from gateway.config import Settings, User
from gateway.router import (
    DirectRecipient,
    GroupRecipient,
    Inbound,
    Router,
    chat_id,
    match_user,
)
from gateway.store import Store
from turn_contract import Health, ToolCall, TurnResponse, Usage

OWNER = "+15551112222"
ALEX = "+15559998888"


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
        users=(User("Keith", OWNER, "uuid-owner"), User("Alex", ALEX)),
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


class FakeGate:
    def __init__(self, allowed=()):
        self.ok = set(allowed)

    async def allowed(self, group_id):
        return group_id in self.ok


@pytest.fixture
def r(settings, store, fake):
    return Router(settings, store, fake, FakeGate({"G1", "G2"}))


def inb(text="hi", number=OWNER, uuid=None, group=None):
    return Inbound(number, uuid, text, 1, group)


async def ask(r, i):
    rep = await r.handle(i)
    return rep.messages if rep else []


def rows(settings):
    c = sqlite3.connect(settings.db_path)
    return c.execute("select status, tool_calls, input_tokens, output_tokens from turns").fetchall()


async def test_dm_from_stranger_dropped(r, fake):
    assert await r.handle(inb(number="+15550000000")) is None and fake.calls == []


async def test_group_not_allowed_dropped(r, fake):
    assert await r.handle(inb(group="G9")) is None and fake.calls == []


async def test_group_from_stranger_dropped(r, fake):
    assert await r.handle(inb(number="+15550000000", group="G1")) is None and fake.calls == []


async def test_gate_error_fails_closed(settings, store, fake):
    class Boom:
        async def allowed(self, group_id):
            raise RuntimeError("lookup failed")

    router = Router(settings, store, fake, Boom())
    assert await router.handle(inb(group="G1")) is None and fake.calls == []


async def test_match_by_uuid_when_number_missing(r, settings):
    assert await ask(r, inb(number=None, uuid="uuid-owner")) == ["ok"]
    assert await r.handle(inb(number=None, uuid="other")) is None
    assert match_user(inb(number=None, uuid="uuid-owner"), settings).name == "Keith"


async def test_reply_addressing(r):
    rep = await r.handle(inb())
    assert rep.chat_id == f"dm:{OWNER}" and rep.recipient == DirectRecipient(OWNER)
    rep = await r.handle(inb(group="G1"))
    assert rep.chat_id == "group:G1" and rep.recipient == GroupRecipient("G1")


async def test_dm_reply_goes_to_sender_uuid(r):
    # signal-cli can't send to a number the person keeps undiscoverable; their uuid always works.
    rep = await r.handle(inb(uuid="uuid-owner"))
    assert rep.chat_id == f"dm:{OWNER}" and rep.recipient == DirectRecipient("uuid-owner")
    rep = await r.handle(inb(number=None, uuid="uuid-owner"))
    assert rep.chat_id == f"dm:{OWNER}" and rep.recipient == DirectRecipient("uuid-owner")


def test_chat_id(settings):
    k = settings.users[0]
    assert chat_id(inb(), k) == f"dm:{OWNER}" and chat_id(inb(group="G1"), k) == "group:G1"


async def test_on_admitted_called_only_when_admitted(r):
    seen = []

    async def cb(recipient):
        seen.append(recipient)

    await r.handle(inb(number="+15550000000"), cb)
    await r.handle(inb(group="G9"), cb)
    assert seen == []
    await r.handle(inb(), cb)
    assert seen == [DirectRecipient(OWNER)]


async def test_dm_from_each_user_is_separate_chat(r, fake, settings):
    await r.handle(inb("a", number=OWNER))
    await r.handle(inb("b", number=ALEX))
    assert fake.calls[0][1].session_id != fake.calls[1][1].session_id
    c = sqlite3.connect(settings.db_path)
    got = c.execute("select chat_id, sender_name from turns order by id").fetchall()
    assert got == [(f"dm:{OWNER}", "Keith"), (f"dm:{ALEX}", "Alex")]


async def test_turn_request_has_sender_name(r, fake):
    await r.handle(inb("hi", number=ALEX))
    await r.handle(inb("hi", number=None, uuid="uuid-owner"))
    assert [c[1].sender_name for c in fake.calls] == ["Alex", "Keith"]


async def test_commands_are_per_chat(r, fake, store):
    await r.handle(inb("/agent deep", group="G1"))
    await r.handle(inb("x", group="G1"))
    await r.handle(inb("x", group="G2"))
    await r.handle(inb("x"))
    assert [c[0] for c in fake.calls] == ["deep", "pi", "pi"]
    g1, g2 = store.current_session("group:G1", "deep"), store.current_session("group:G2", "pi")
    dm = store.current_session(f"dm:{OWNER}", "pi")
    await r.handle(inb("/agent pi", group="G1"))
    g1pi = store.current_session("group:G1", "pi")
    await r.handle(inb("/new", group="G1"))
    assert store.current_session("group:G1", "pi") != g1pi
    assert store.current_session("group:G1", "deep") == g1
    assert store.current_session("group:G2", "pi") == g2
    assert store.current_session(f"dm:{OWNER}", "pi") == dm


async def test_routes_to_default_agent_with_session_and_now(r, fake, store):
    await ask(r, inb("what?"))
    agent, req = fake.calls[0]
    assert agent == "pi" and req.text == "what?"
    assert req.session_id == store.current_session(f"dm:{OWNER}", "pi")
    assert req.now.tzinfo.key == "America/New_York" and req.now.microsecond == 0
    assert isinstance(req.now, datetime) and req.message_id


async def test_agent_command_switches(r, fake):
    out = await ask(r, inb("/agent deep"))
    assert len(out) == 1 and "deep" in out[0]
    await ask(r, inb("hello"))
    assert fake.calls[0][0] == "deep"


async def test_unknown_agent_lists_choices(r, fake):
    out = await ask(r, inb("/agent foo"))
    assert len(out) == 1
    assert "foo" in out[0] and "deep" in out[0] and "echo" in out[0] and "pi" in out[0]
    await ask(r, inb("hello"))
    assert fake.calls[0][0] == "pi"


async def test_new_rotates_only_current_agent(r, store):
    pi, deep = store.current_session(f"dm:{OWNER}", "pi"), store.current_session(f"dm:{OWNER}", "deep")
    out = await ask(r, inb("/new"))
    assert len(out) == 1
    assert store.current_session(f"dm:{OWNER}", "pi") != pi
    assert store.current_session(f"dm:{OWNER}", "deep") == deep


async def test_which_shows_agent_session_model(r, store):
    out = await ask(r, inb("/which"))
    assert "pi" in out[0] and store.current_session(f"dm:{OWNER}", "pi") in out[0] and "model-x" in out[0]


async def test_which_survives_agent_down(r, store, fake):
    fake.health_exc = AgentDown("pi")
    out = await ask(r, inb("/which"))
    assert "pi" in out[0] and store.current_session(f"dm:{OWNER}", "pi") in out[0]
    assert "unavailable" in out[0]


async def test_help_lists_commands(r):
    out = (await ask(r, inb("/help")))[0]
    for c in ("/agent", "/new", "/which", "/help"):
        assert c in out


async def test_unknown_slash_goes_to_agent(r, fake):
    await ask(r, inb("/spending"))
    assert fake.calls[0][1].text == "/spending"


async def test_timeout_copy(r, fake, settings):
    fake.exc = AgentTimeout()
    assert await ask(r, inb()) == ["That took too long. Try again or /new."]
    assert rows(settings)[0][0] == "timeout"


async def test_down_copy(r, fake, settings):
    fake.exc = AgentDown("pi")
    assert await ask(r, inb()) == ["The pi agent is down."]
    assert rows(settings)[0][0] == "error"


async def test_monarch_auth_copy(r, fake):
    fake.exc = AgentError("monarch_auth", "expired")
    assert await ask(r, inb()) == ["Monarch login expired. Run `mise run monarch-login` on the Mac."]


async def test_other_error_copy_and_logged(r, fake, settings):
    fake.exc = AgentError("internal", "boom")
    assert await ask(r, inb()) == ["The pi agent is down."]
    assert rows(settings)[0][0] == "error"


async def test_agent_error_timeout_code(r, fake):
    fake.exc = AgentError("timeout", "slow")
    assert await ask(r, inb()) == ["That took too long. Try again or /new."]


async def test_long_reply_split(r, fake):
    fake.reply = TurnResponse(reply=("a" * 1000 + "\n\n") * 3)
    assert len(await ask(r, inb())) >= 2


async def test_turn_logged(r, fake, settings):
    fake.reply = TurnResponse(
        reply="done",
        tool_calls=[ToolCall(name="GetAccounts", args={}, ms=5, ok=True)],
        usage=Usage(input_tokens=11, output_tokens=7),
    )
    await ask(r, inb())
    got = rows(settings)
    assert len(got) == 1
    assert got[0][0] == "ok" and "GetAccounts" in got[0][1] and got[0][2:] == (11, 7)


async def test_commands_not_logged(r, settings):
    await ask(r, inb("/new"))
    assert rows(settings) == []


async def test_known_uuid_with_unknown_number_does_not_match(settings):
    assert match_user(inb(number="+19995550000", uuid="uuid-owner"), settings) is None


async def test_uuid_match_is_case_insensitive(settings):
    assert match_user(inb(number=None, uuid="UUID-OWNER"), settings).name == "Keith"
