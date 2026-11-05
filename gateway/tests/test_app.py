import asyncio
import json
import logging
from pathlib import Path

import pytest
from gateway.app import Gateway
from gateway.config import Settings, User
from gateway.router import DirectRecipient, Inbound, Reply, Router
from gateway.signal_client import parse_envelope
from gateway.store import Store
from turn_contract import TurnResponse

OWNER = "+15551112222"
ALEX = "+15559998888"
DM = DirectRecipient(OWNER)
FIX = Path(__file__).parent / "fixtures"


def raw(text="hi", number=OWNER):
    d = json.loads((FIX / "data_message.json").read_text())
    d["envelope"]["sourceNumber"] = number
    d["envelope"]["source"] = number
    d["envelope"]["sourceUuid"] = "u-" + number
    d["envelope"]["dataMessage"]["message"] = text
    return d


class FakeSignal:
    def __init__(self):
        self.calls = []

    async def send(self, recipient, text):
        self.calls.append(("send", recipient, text))

    async def typing(self, recipient, on):
        self.calls.append(("typing", recipient, on))


class FakeRouter:
    def __init__(self, delay=0.0, exc=None):
        self.delay, self.exc, self.events = delay, exc, []

    async def handle(self, inb: Inbound, on_admitted=None):
        self.events.append(("start", inb.text))
        rec = DirectRecipient(inb.sender_number)
        admitted = inb.sender_number in (OWNER, ALEX) and inb.group_id is None
        if admitted and on_admitted:
            await on_admitted(rec)
        await asyncio.sleep(self.delay)
        self.events.append(("end", inb.text))
        if self.exc and inb.text != "ok":
            raise self.exc
        if not admitted:
            return None
        return Reply(f"dm:{inb.sender_number}", rec, [f"r:{inb.text}"])


@pytest.fixture
def settings(tmp_path):
    return Settings(
        users=(User("Keith", OWNER), User("Alex", ALEX)),
        bot_number="+15553334444",
        signal_url="http://signal",
        agents={"echo": "http://e"},
        default_agent="echo",
        db_path=tmp_path / "g.db",
    )


async def process(gw, *frames):
    for f in frames:
        gw.submit(f)
    await gw.drain()


async def test_messages_in_one_chat_processed_serially(settings):
    sig, router = FakeSignal(), FakeRouter(delay=0.05)
    gw = Gateway(settings, sig, router)
    await process(gw, raw("a"), raw("b"))
    assert router.events == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]
    assert [c[2] for c in sig.calls if c[0] == "send"] == ["r:a", "r:b"]


async def test_typing_on_then_off(settings):
    sig = FakeSignal()
    await process(Gateway(settings, sig, FakeRouter()), raw())
    assert sig.calls == [("typing", DM, True), ("send", DM, "r:hi"), ("typing", DM, False)]


async def test_typing_off_even_on_error(settings):
    sig = FakeSignal()
    await process(Gateway(settings, sig, FakeRouter(exc=RuntimeError("boom"))), raw())
    assert sig.calls == [("typing", DM, True), ("typing", DM, False)]


async def test_typing_off_failure_does_not_escape(settings, caplog):
    class Sig(FakeSignal):
        async def typing(self, recipient, on):
            await super().typing(recipient, on)
            if not on:
                raise RuntimeError("typing off broke")

    sig = Sig()
    gw = Gateway(settings, sig, FakeRouter())
    with caplog.at_level(logging.WARNING):
        await gw.handle(parse_envelope(raw("a")))  # must not raise
        await process(gw, raw("b"))
    assert [c[2] for c in sig.calls if c[0] == "send"] == ["r:a", "r:b"]
    assert any("typing" in r.getMessage() for r in caplog.records)


async def test_unexpected_error_logged_and_swallowed(settings, caplog):
    sig = FakeSignal()
    gw = Gateway(settings, sig, FakeRouter(exc=RuntimeError("boom")))
    with caplog.at_level(logging.ERROR):
        await process(gw, raw())  # must not raise
    assert any(r.exc_info and "boom" in str(r.exc_info[1]) for r in caplog.records)
    assert not [c for c in sig.calls if c[0] == "send"]


async def test_crashing_turn_does_not_kill_chat_worker(settings, caplog):
    sig, router = FakeSignal(), FakeRouter(exc=RuntimeError("boom"))
    gw = Gateway(settings, sig, router)
    with caplog.at_level(logging.ERROR):
        await process(gw, raw("a"), raw("ok"))
    assert [c[2] for c in sig.calls if c[0] == "send"] == ["r:ok"]


async def test_worker_survives_base_exception_in_turn(settings):
    class Cancelling(FakeRouter):
        async def handle(self, inb, on_admitted=None):
            if inb.text == "cancel":
                raise asyncio.CancelledError
            return await super().handle(inb, on_admitted)

    sig = FakeSignal()
    gw = Gateway(settings, sig, Cancelling())
    gw.submit(raw("cancel"))
    await asyncio.sleep(0.01)
    await process(gw, raw("after"))
    assert [c[2] for c in sig.calls if c[0] == "send"] == ["r:after"]


async def test_idle_workers_exit(settings):
    gw = Gateway(settings, FakeSignal(), FakeRouter(), idle_s=0.02)
    await process(gw, raw("a"), raw("b", number=ALEX))
    assert gw.active_chats() == {f"dm:{OWNER}", f"dm:{ALEX}"}
    await asyncio.sleep(0.1)
    assert gw.active_chats() == set()
    await process(gw, raw("c"))  # a new worker starts on demand
    assert gw.active_chats() == {f"dm:{OWNER}"}


async def test_stranger_no_typing(settings):
    sig = FakeSignal()
    gw = Gateway(settings, sig, FakeRouter())
    await process(gw, raw(number="+19998887777"))
    assert sig.calls == []
    assert gw.active_chats() == set()  # strangers never get a worker


async def test_non_text_ignored(settings):
    sig = FakeSignal()
    receipt = json.loads((FIX / "receipt.json").read_text())
    await process(Gateway(settings, sig, FakeRouter()), receipt)
    assert sig.calls == []


async def test_run_survives_handler_error(settings):
    class Sig(FakeSignal):
        async def receive(self):
            yield raw("a")
            yield raw("b")

    sig, router = Sig(), FakeRouter(exc=RuntimeError("boom"))
    await Gateway(settings, sig, router).run()
    assert [e for e in router.events if e[0] == "start"] == [("start", "a"), ("start", "b")]


WEIRD_FRAMES = [
    None,
    "not a dict",
    ["a", "list"],
    42,
    {"envelope": {"sourceNumber": OWNER, "dataMessage": {"message": "hi", "groupInfo": object}}},
    {"envelope": {"sourceNumber": ["x"], "dataMessage": {"message": "hi"}}},
]


@pytest.mark.parametrize("frame", WEIRD_FRAMES)
async def test_malformed_frame_logged_and_skipped(settings, caplog, frame):
    sig = FakeSignal()
    with caplog.at_level(logging.WARNING):
        await process(Gateway(settings, sig, FakeRouter()), frame)  # must not raise
    assert not [c for c in sig.calls if c[0] == "send"]
    if not isinstance(frame, dict):
        assert any("malformed Signal frame" in r.getMessage() for r in caplog.records)


async def test_run_survives_malformed_frame(settings, caplog):
    class Sig(FakeSignal):
        async def receive(self):
            yield "garbage"
            yield raw("b")

    sig, router = Sig(), FakeRouter()
    with caplog.at_level(logging.ERROR):
        await Gateway(settings, sig, router).run()
    assert router.events == [("start", "b"), ("end", "b")]
    assert any("malformed Signal frame" in r.getMessage() for r in caplog.records)


# --- concurrency with the real router ----------------------------------------


class SlowAgents:
    """A fake agent whose turn takes as long as the message says ("slow" = 0.2s)."""

    def __init__(self):
        self.log: list[tuple[str, str]] = []

    async def turn(self, agent, req):
        self.log.append(("start", req.text))
        await asyncio.sleep(0.2 if req.text.endswith("slow") else 0.0)
        self.log.append(("end", req.text))
        return TurnResponse(reply=f"r:{req.text}")

    async def health(self, agent):  # pragma: no cover - unused
        raise NotImplementedError


class NoGroups:
    async def allowed(self, group_id):
        return False


async def test_chats_concurrent_messages_ordered(settings):
    sig, agents = FakeSignal(), SlowAgents()
    router = Router(settings, Store(settings.db_path), agents, NoGroups())
    gw = Gateway(settings, sig, router)
    gw.submit(raw("A1 slow"))  # chat A = Keith's DM
    gw.submit(raw("A2"))
    gw.submit(raw("B1", number=ALEX))  # chat B = Alex's DM
    await gw.drain()
    sends = [c[2] for c in sig.calls if c[0] == "send"]
    assert sends == ["r:B1", "r:A1 slow", "r:A2"]  # B didn't wait for A; A stays in order
    a = [e for e in agents.log if e[1].startswith("A")]
    assert a == [("start", "A1 slow"), ("end", "A1 slow"), ("start", "A2"), ("end", "A2")]


async def test_receive_loop_not_blocked_by_turn(settings):
    release = asyncio.Event()
    seen: list[str] = []

    class BlockingRouter(FakeRouter):
        async def handle(self, inb, on_admitted=None):
            seen.append(inb.text)
            if inb.text == "A":
                await release.wait()  # blocks until the receive loop has moved on
            return await super().handle(inb, on_admitted)

    class Sig(FakeSignal):
        async def receive(self):
            yield raw("A")
            await asyncio.sleep(0)  # let A's worker start
            assert seen == ["A"]
            yield raw("B", number=ALEX)
            await asyncio.sleep(0.01)
            assert seen == ["A", "B"]  # B was handled while A was still in its turn
            release.set()

    sig = Sig()
    await asyncio.wait_for(Gateway(settings, sig, BlockingRouter()).run(), timeout=2)
    assert [c[2] for c in sig.calls if c[0] == "send"] == ["r:B", "r:A"]
