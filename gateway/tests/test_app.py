import asyncio
import json
import logging
from pathlib import Path

import pytest
from gateway.app import Gateway
from gateway.config import Settings
from gateway.router import Inbound

OWNER = "+15551112222"
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

    async def handle(self, inb: Inbound):
        self.events.append(("start", inb.text))
        await asyncio.sleep(self.delay)
        self.events.append(("end", inb.text))
        if self.exc:
            raise self.exc
        if inb.sender_number != OWNER:
            return []
        return [f"r:{inb.text}"]


@pytest.fixture
def settings(tmp_path):
    return Settings(
        owner_number=OWNER,
        owner_uuid=None,
        bot_number="+15553334444",
        signal_url="http://signal",
        agents={"echo": "http://e"},
        default_agent="echo",
        db_path=tmp_path / "g.db",
    )


async def test_messages_processed_serially(settings):
    sig, router = FakeSignal(), FakeRouter(delay=0.05)
    gw = Gateway(settings, sig, router)
    await asyncio.gather(gw.on_message(raw("a")), gw.on_message(raw("b")))
    assert router.events == [("start", "a"), ("end", "a"), ("start", "b"), ("end", "b")]
    assert [c[2] for c in sig.calls if c[0] == "send"] == ["r:a", "r:b"]


async def test_typing_on_then_off(settings):
    sig = FakeSignal()
    await Gateway(settings, sig, FakeRouter()).on_message(raw())
    assert sig.calls == [("typing", OWNER, True), ("send", OWNER, "r:hi"), ("typing", OWNER, False)]


async def test_typing_off_even_on_error(settings):
    sig = FakeSignal()
    await Gateway(settings, sig, FakeRouter(exc=RuntimeError("boom"))).on_message(raw())
    assert sig.calls == [("typing", OWNER, True), ("typing", OWNER, False)]


async def test_unexpected_error_logged_and_swallowed(settings, caplog):
    sig = FakeSignal()
    gw = Gateway(settings, sig, FakeRouter(exc=RuntimeError("boom")))
    with caplog.at_level(logging.ERROR):
        await gw.on_message(raw())  # must not raise
    assert any(r.exc_info and "boom" in str(r.exc_info[1]) for r in caplog.records)
    assert not [c for c in sig.calls if c[0] == "send"]


async def test_non_owner_no_typing(settings):
    sig = FakeSignal()
    await Gateway(settings, sig, FakeRouter()).on_message(raw(number="+19998887777"))
    assert sig.calls == []


async def test_non_text_ignored(settings):
    sig = FakeSignal()
    receipt = json.loads((FIX / "receipt.json").read_text())
    await Gateway(settings, sig, FakeRouter()).on_message(receipt)
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
        await Gateway(settings, sig, FakeRouter()).on_message(frame)  # must not raise
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
