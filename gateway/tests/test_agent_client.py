import httpx
import pytest
import respx
from gateway.agent_client import AgentDown, AgentError, AgentTimeout, HttpAgentClient
from turn_contract import TurnRequest

URL = "http://agent:8201"
REQ = TurnRequest(session_id="s", message_id="m", text="hi", now="2026-11-04T10:00:00-05:00")


@pytest.fixture
def client():
    return HttpAgentClient({"echo": URL}, timeout_s=5)


@respx.mock
async def test_ok(client):
    respx.post(f"{URL}/turn").respond(200, json={"reply": "echo: hi"})
    assert (await client.turn("echo", REQ)).reply == "echo: hi"


@respx.mock
async def test_timeout(client):
    respx.post(f"{URL}/turn").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AgentTimeout):
        await client.turn("echo", REQ)


@respx.mock
async def test_down(client):
    respx.post(f"{URL}/turn").mock(side_effect=httpx.ConnectError("no"))
    with pytest.raises(AgentDown):
        await client.turn("echo", REQ)


@respx.mock
async def test_monarch_auth(client):
    body = {"error": {"code": "monarch_auth", "message": "login"}}
    respx.post(f"{URL}/turn").respond(502, json=body)
    with pytest.raises(AgentError) as ei:
        await client.turn("echo", REQ)
    assert ei.value.code == "monarch_auth"


@respx.mock
async def test_agent_timeout_code(client):
    respx.post(f"{URL}/turn").respond(504, json={"error": {"code": "timeout", "message": "t"}})
    with pytest.raises(AgentTimeout):
        await client.turn("echo", REQ)


@respx.mock
async def test_other_non_2xx_is_down(client):
    respx.post(f"{URL}/turn").respond(503, text="bad gateway html")
    with pytest.raises(AgentDown):
        await client.turn("echo", REQ)


@respx.mock
async def test_health_ok(client):
    respx.get(f"{URL}/health").respond(200, json={"name": "echo", "framework": "e", "model": "m"})
    assert (await client.health("echo")).model == "m"


@respx.mock
async def test_health_down(client):
    respx.get(f"{URL}/health").mock(side_effect=httpx.ConnectError("no"))
    with pytest.raises(AgentDown):
        await client.health("echo")


async def test_unknown_agent(client):
    with pytest.raises(AgentDown):
        await client.turn("nope", REQ)


@respx.mock
@pytest.mark.parametrize(
    "kw",
    [
        {"json": {"unexpected": "shape"}},  # ValidationError: reply missing
        {"json": {"reply": 42}},  # ValidationError: wrong type
        {"text": "<html>not json</html>"},  # ValueError: not JSON
    ],
)
async def test_malformed_200_body_is_down(client, kw):
    respx.post(f"{URL}/turn").respond(200, **kw)
    with pytest.raises(AgentDown):
        await client.turn("echo", REQ)


@respx.mock
async def test_malformed_200_health_is_down(client):
    respx.get(f"{URL}/health").respond(200, json={"name": "echo"})
    with pytest.raises(AgentDown):
        await client.health("echo")
