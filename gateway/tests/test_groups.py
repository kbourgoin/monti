import asyncio
import dataclasses
import json
import logging
from pathlib import Path

import pytest
from gateway.app import Gateway
from gateway.config import Settings, User
from gateway.groups import GroupEvents, MembershipGate
from gateway.router import GroupRecipient, Inbound, Router
from gateway.signal_client import GroupInfo, GroupUpdate, MemberRef
from gateway.store import Store
from turn_contract import TurnResponse

FIX = Path(__file__).parent / "fixtures"
KEITH = "+16175550100"
ALEX = "+16175550101"
BOT = "+15553334444"
OUTSIDER = "+16175559999"
G1 = "UiRIyG9CIM/7fvTRKAEQF6QwC7RTkH5qQEW3uiERjp8="  # Keith, Alex (uuid only), bot
G2 = "mUUpE/hoVDLAgHVFTB56rbPVQSqZc+rpMR0DAArT0SQ="  # Keith, an outsider, bot


def M(number=None, uuid=None):
    return MemberRef(number, uuid)


def g1():
    return GroupInfo(
        G1, [M(KEITH, "uuid-keith"), M(None, "uuid-alex"), M(BOT, "uuid-bot")], True, []
    )


def g2():
    return GroupInfo(
        G2, [M(KEITH, "uuid-keith"), M(OUTSIDER, "uuid-outsider"), M(BOT, "uuid-bot")], True, []
    )


class FakeSignal:
    def __init__(self, groups=None, exc=None, delay=0.0):
        self.groups = [g1(), g2()] if groups is None else groups
        self.exc, self.delay = exc, delay
        self.fetches = 0
        self.joined: list[str] = []
        self.calls: list[tuple] = []

    async def list_groups(self):
        self.fetches += 1
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return list(self.groups)

    async def join_group(self, group_id):
        self.joined.append(group_id)

    async def send(self, recipient, text):
        self.calls.append(("send", recipient, text))

    async def typing(self, recipient, on):
        self.calls.append(("typing", recipient, on))


class FakeAgents:
    def __init__(self):
        self.calls = []

    async def turn(self, agent, req):
        self.calls.append(req)
        return TurnResponse(reply=f"r:{req.text}")

    async def health(self, agent):  # pragma: no cover - unused
        raise NotImplementedError


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def settings(tmp_path):
    return Settings(
        users=(User("Keith", KEITH, "uuid-keith"), User("Alex", ALEX, "uuid-alex")),
        bot_number=BOT,
        signal_url="http://signal",
        agents={"echo": "http://e"},
        default_agent="echo",
        db_path=tmp_path / "g.db",
    )


def router_for(settings, gate, agents=None):
    return Router(settings, Store(settings.db_path), agents or FakeAgents(), gate)


def ginb(text="hi", number=KEITH, uuid="uuid-keith", group=G1):
    return Inbound(number, uuid, text, 1, group)


# --- admission ---------------------------------------------------------------


async def test_group_all_users_admitted(settings):
    agents = FakeAgents()
    r = router_for(settings, MembershipGate(FakeSignal(), settings), agents)
    reply = await r.handle(ginb("how much?"))
    assert reply is not None
    assert reply.chat_id == f"group:{G1}"
    assert reply.recipient == GroupRecipient(G1)
    assert reply.messages == ["r:how much?"]
    assert agents.calls[0].sender_name == "Keith"


async def test_group_with_outsider_silent(settings):
    agents = FakeAgents()
    r = router_for(settings, MembershipGate(FakeSignal(), settings), agents)
    assert await r.handle(ginb(group=G2)) is None
    assert await r.handle(ginb("/which", group=G2)) is None
    assert agents.calls == []


@pytest.mark.parametrize("failure", ["raise", "timeout"])
async def test_group_lookup_failure_silent(settings, caplog, failure):
    sig = (
        FakeSignal(exc=RuntimeError("signal down")) if failure == "raise" else FakeSignal(delay=1.0)
    )
    gate = MembershipGate(sig, settings, timeout_s=0.05)
    agents = FakeAgents()
    with caplog.at_level(logging.WARNING):
        assert await gate.allowed(G1) is False
        assert await router_for(settings, gate, agents).handle(ginb()) is None
    assert agents.calls == []
    assert any("membership" in r.getMessage().lower() for r in caplog.records)


async def test_group_lookup_failure_not_cached(settings):
    sig = FakeSignal(exc=RuntimeError("signal down"))
    gate = MembershipGate(sig, settings)
    assert await gate.allowed(G1) is False
    sig.exc = None
    assert await gate.allowed(G1) is True


async def test_group_membership_cached_60s(settings):
    sig, clock = FakeSignal(), Clock()
    gate = MembershipGate(sig, settings, ttl_s=60, clock=clock)
    assert await gate.allowed(G1) is True
    assert sig.fetches == 1
    clock.t += 59
    assert await gate.allowed(G1) is True
    assert await gate.allowed(G2) is False  # one fetch fills every group
    assert sig.fetches == 1
    # Membership changes are seen once the entry expires.
    sig.groups = [GroupInfo(G1, g1().members + [M(OUTSIDER, None)], True, [])]
    clock.t += 2
    assert await gate.allowed(G1) is False
    assert sig.fetches == 2


async def test_group_unknown_group_silent_and_cached(settings):
    sig, clock = FakeSignal(), Clock()
    gate = MembershipGate(sig, settings, clock=clock)
    assert await gate.allowed("nope") is False
    assert await gate.allowed("nope") is False
    assert sig.fetches == 1
    assert await gate.allowed("") is False


async def test_group_invalidate_forces_refetch(settings):
    sig = FakeSignal()
    gate = MembershipGate(sig, settings)
    assert await gate.allowed(G1) is True
    sig.groups = [GroupInfo(G1, g1().members + [M(OUTSIDER, None)], True, [])]
    gate.invalidate(G1)
    assert await gate.allowed(G1) is False
    assert sig.fetches == 2


async def test_group_uuid_only_sender(settings):
    agents = FakeAgents()
    r = router_for(settings, MembershipGate(FakeSignal(), settings), agents)
    reply = await r.handle(ginb("hey", number=None, uuid="uuid-alex"))
    assert reply is not None and reply.recipient == GroupRecipient(G1)
    assert reply.chat_id == f"group:{G1}"
    assert agents.calls[0].sender_name == "Alex"


@pytest.mark.parametrize(
    "members, pending, is_member, expected",
    [
        ([M(KEITH), M(BOT)], [], True, True),  # Keith and Monty alone
        ([M(KEITH), M(None, "uuid-alex"), M(BOT)], [], True, True),
        # A bot uuid that settings don't know is an outsider.
        ([M(KEITH), M(None, "uuid-bot2")], [], True, False),
        ([M(KEITH), M(None, None), M(BOT)], [], True, False),  # unidentifiable member
        # A number that isn't a user's wins over a matching uuid.
        ([M(KEITH), M("+16175550111", "uuid-alex"), M(BOT)], [], True, False),
        ([M(KEITH), M(BOT)], [M(None, "uuid-stranger")], True, False),  # outsider invited
        ([M(KEITH), M(BOT)], [M(ALEX)], True, True),  # user invited
        ([M(KEITH), M(BOT)], [], False, False),  # Monty is not (yet) a member
        ([M(BOT)], [], True, True),
    ],
)
async def test_gate_member_rules(settings, members, pending, is_member, expected):
    gate = MembershipGate(FakeSignal([GroupInfo(G1, members, is_member, pending)]), settings)
    assert await gate.allowed(G1) is expected


async def test_gate_excludes_bot_by_uuid(settings):
    s = dataclasses.replace(settings, bot_uuid="uuid-bot")
    gate = MembershipGate(FakeSignal([GroupInfo(G1, [M(KEITH), M(None, "uuid-bot")], True, [])]), s)
    assert await gate.allowed(G1) is True


# --- invites -----------------------------------------------------------------


async def test_invite_from_user_accepted(settings):
    sig = FakeSignal([GroupInfo(G1, [M(KEITH)], False, [M(BOT)])])
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    await events.on_update(GroupUpdate(KEITH, "uuid-keith", G1))
    assert sig.joined == [G1]


async def test_invite_from_uuid_only_user_accepted(settings):
    sig = FakeSignal([GroupInfo(G1, [M(None, "uuid-alex")], False, [M(BOT)])])
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    await events.on_update(GroupUpdate(None, "uuid-alex", G1))
    assert sig.joined == [G1]


async def test_invite_from_outsider_ignored(settings):
    sig = FakeSignal([GroupInfo(G1, [M(OUTSIDER)], False, [M(BOT)])])
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    await events.on_update(GroupUpdate(OUTSIDER, "uuid-outsider", G1))
    assert sig.joined == []
    assert sig.fetches == 0


async def test_update_when_already_member_does_not_join(settings):
    sig = FakeSignal()
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    await events.on_update(GroupUpdate(KEITH, "uuid-keith", G1))
    assert sig.joined == []


async def test_update_invalidates_membership_cache(settings):
    sig = FakeSignal()
    gate = MembershipGate(sig, settings)
    events = GroupEvents(sig, settings, gate)
    assert await gate.allowed(G1) is True
    sig.groups = [GroupInfo(G1, g1().members + [M(OUTSIDER, None)], True, [])]
    # e.g. someone joined via a group link
    await events.on_update(GroupUpdate(OUTSIDER, None, G1))
    assert await gate.allowed(G1) is False


async def test_invite_ignored_when_outsider_is_member(settings):
    # An outsider made the group and invited Monty; Keith's later rename must not make Monty join.
    sig = FakeSignal([GroupInfo(G1, [M(KEITH), M(OUTSIDER)], False, [M(BOT)])])
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    await events.on_update(GroupUpdate(KEITH, "uuid-keith", G1))
    assert sig.joined == []


async def test_invite_ignored_when_outsider_also_pending(settings):
    sig = FakeSignal([GroupInfo(G1, [M(KEITH)], False, [M(BOT), M(None, "uuid-stranger")])])
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    await events.on_update(GroupUpdate(KEITH, "uuid-keith", G1))
    assert sig.joined == []


async def test_no_join_when_bot_not_pending(settings):
    # e.g. a user removed Monty: not a member, and not invited either.
    sig = FakeSignal([GroupInfo(G1, [M(KEITH), M(None, "uuid-alex")], False, [])])
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    await events.on_update(GroupUpdate(KEITH, "uuid-keith", G1))
    assert sig.joined == []


async def test_invite_all_users_bot_pending_joined(settings):
    sig = FakeSignal([GroupInfo(G1, [M(KEITH), M(None, "uuid-alex")], False, [M(BOT, "uuid-bot")])])
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    await events.on_update(GroupUpdate(None, "uuid-alex", G1))
    assert sig.joined == [G1]


async def test_join_refreshes_gate(settings):
    class Joining(FakeSignal):
        async def join_group(self, group_id):
            await super().join_group(group_id)
            self.groups = [GroupInfo(G1, [M(KEITH), M(BOT)], True, [])]

    sig = Joining([GroupInfo(G1, [M(KEITH)], False, [M(BOT)])])
    gate = MembershipGate(sig, settings)
    await GroupEvents(sig, settings, gate).on_update(GroupUpdate(KEITH, "uuid-keith", G1))
    assert sig.joined == [G1]
    assert await gate.allowed(G1) is True  # not the pre-join "invited only" snapshot


async def test_invite_lookup_timeout_fails_closed(settings, caplog):
    sig = FakeSignal([GroupInfo(G1, [M(KEITH)], False, [M(BOT)])], delay=1.0)
    events = GroupEvents(sig, settings, MembershipGate(sig, settings, timeout_s=0.05))
    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(events.on_update(GroupUpdate(KEITH, None, G1)), timeout=0.5)
    assert sig.joined == []
    assert any("invite" in r.getMessage() for r in caplog.records)


async def test_invite_lookup_failure_logged(settings, caplog):
    sig = FakeSignal(exc=RuntimeError("down"))
    events = GroupEvents(sig, settings, MembershipGate(sig, settings))
    with caplog.at_level(logging.WARNING):
        await events.on_update(GroupUpdate(KEITH, None, G1))  # must not raise
    assert sig.joined == []
    assert caplog.records


# --- through the gateway -----------------------------------------------------


def group_frame(text="hi", number=KEITH, uuid="uuid-keith", group=G1):
    d = json.loads((FIX / "group_message.json").read_text())
    d["envelope"]["sourceNumber"] = d["envelope"]["source"] = number
    d["envelope"]["sourceUuid"] = uuid
    d["envelope"]["dataMessage"]["message"] = text
    d["envelope"]["dataMessage"]["groupInfo"]["groupId"] = group
    return d


async def test_group_reply_and_typing_go_to_group(settings):
    sig = FakeSignal()
    gate = MembershipGate(sig, settings)
    gw = Gateway(settings, sig, router_for(settings, gate), GroupEvents(sig, settings, gate))
    gw.submit(group_frame("q"))
    gw.submit(group_frame("x", group=G2))  # outsider group: nothing at all
    await gw.drain()
    to = GroupRecipient(G1)
    assert sig.calls == [("typing", to, True), ("send", to, "r:q"), ("typing", to, False)]


async def test_gateway_routes_group_update_to_events(settings):
    sig = FakeSignal([GroupInfo(G1, [M(KEITH)], False, [M(BOT)])])
    gate = MembershipGate(sig, settings)
    gw = Gateway(settings, sig, router_for(settings, gate), GroupEvents(sig, settings, gate))
    gw.submit(json.loads((FIX / "group_update.json").read_text()))
    await gw.drain()
    assert sig.joined == [G1]
    assert sig.calls == []


async def test_outsider_added_mid_turn_blocks_reply(settings):
    release = asyncio.Event()
    started = asyncio.Event()

    class Blocking(FakeAgents):
        async def turn(self, agent, req):
            started.set()
            await release.wait()
            return await super().turn(agent, req)

    sig = FakeSignal()
    gate = MembershipGate(sig, settings)
    gw = Gateway(
        settings, sig, router_for(settings, gate, Blocking()), GroupEvents(sig, settings, gate)
    )
    gw.submit(group_frame("q"))
    await asyncio.wait_for(started.wait(), 1)
    sig.groups = [GroupInfo(G1, g1().members + [M(OUTSIDER, None)], True, [])]
    gw.submit(json.loads((FIX / "group_update.json").read_text()))  # invalidates synchronously
    release.set()
    await gw.drain()
    assert [c for c in sig.calls if c[0] == "send"] == []


async def test_inflight_listing_does_not_repopulate_invalidated_group(settings):
    sig = FakeSignal(delay=0.05)
    gate = MembershipGate(sig, settings)
    task = asyncio.create_task(gate.lookup(G1))
    await asyncio.sleep(0.01)
    gate.invalidate(G2)
    await task
    assert G2 not in gate._cache
    assert G1 in gate._cache


async def test_uuid_only_sender_in_outsider_group_silent(settings):
    agents = FakeAgents()
    r = router_for(settings, MembershipGate(FakeSignal(), settings), agents)
    assert await r.handle(ginb(number=None, uuid="uuid-alex", group=G2)) is None
    assert agents.calls == []


async def test_uppercase_uuid_in_users_matches(settings):
    s = dataclasses.replace(
        settings, users=(User("Keith", KEITH, "uuid-keith"), User("Alex", ALEX, "uuid-alex"))
    )
    from gateway.config import parse_users

    users = parse_users(f"Keith={KEITH}/UUID-KEITH,Alex={ALEX}/UUID-ALEX")
    s = dataclasses.replace(s, users=users, bot_uuid="uuid-bot")
    gate = MembershipGate(FakeSignal(), s)
    assert await gate.allowed(G1) is True  # member uuid-alex matches UUID-ALEX
    r = router_for(s, gate)
    assert await r.handle(ginb(number=None, uuid="UUID-Alex")) is not None
