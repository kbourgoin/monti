import json
from pathlib import Path

import httpx
import pytest
import respx
from gateway.router import Inbound
from gateway.signal_client import SignalClient, parse_envelope

FIX = Path(__file__).parent / "fixtures"


def fixture(name):
    return json.loads((FIX / name).read_text())


def test_parse_envelope_text():
    assert parse_envelope(fixture("data_message.json")) == Inbound(
        "+16175550100", "uuid-sender", "hi", 1730000000000, False
    )


@pytest.mark.parametrize(
    "name", ["receipt", "typing", "reaction", "sync_sent", "attachment_only", "edit"]
)
def test_parse_envelope_ignores_non_text(name):
    assert parse_envelope(fixture(f"{name}.json")) is None


@pytest.mark.parametrize("name", ["group_message", "group_v2_message"])
def test_parse_envelope_group(name):
    inb = parse_envelope(fixture(f"{name}.json"))
    assert inb is not None and inb.is_group is True


def test_parse_envelope_garbage():
    assert parse_envelope({}) is None
    assert parse_envelope({"envelope": {"dataMessage": {"message": ""}}}) is None


BASE = "http://signal:8080"


@respx.mock
async def test_send():
    route = respx.post(f"{BASE}/v2/send").respond(201, json={})
    await SignalClient(BASE, "+1bot").send("+1me", "hello")
    assert json.loads(route.calls[0].request.content) == {
        "message": "hello",
        "number": "+1bot",
        "recipients": ["+1me"],
    }


@respx.mock
async def test_typing_on_off_methods():
    put = respx.put(f"{BASE}/v1/typing-indicator/+1bot").respond(204)
    delete = respx.delete(f"{BASE}/v1/typing-indicator/+1bot").respond(204)
    c = SignalClient(BASE, "+1bot")
    await c.typing("+1me", True)
    await c.typing("+1me", False)
    assert json.loads(put.calls[0].request.content) == {"recipient": "+1me"}
    assert json.loads(delete.calls[0].request.content) == {"recipient": "+1me"}


@respx.mock
async def test_typing_failure_swallowed():
    respx.put(f"{BASE}/v1/typing-indicator/+1bot").mock(side_effect=httpx.ConnectError("x"))
    respx.delete(f"{BASE}/v1/typing-indicator/+1bot").respond(500)
    c = SignalClient(BASE, "+1bot")
    await c.typing("+1me", True)
    await c.typing("+1me", False)
