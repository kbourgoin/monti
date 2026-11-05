from typing import Protocol

import httpx
from pydantic import BaseModel, ValidationError
from turn_contract import ErrorResponse, Health, TurnRequest, TurnResponse


class AgentTimeout(Exception):
    """The agent did not answer within the turn timeout."""


class AgentDown(Exception):
    """The agent could not be reached."""


class AgentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class AgentPort(Protocol):
    async def turn(self, agent: str, req: TurnRequest) -> TurnResponse: ...

    async def health(self, agent: str) -> Health: ...


class HttpAgentClient:
    def __init__(self, agents: dict[str, str], timeout_s: float):
        self.agents = agents
        self._http = httpx.AsyncClient(timeout=timeout_s)

    def _url(self, agent: str, path: str) -> str:
        base = self.agents.get(agent)
        if base is None:
            raise AgentDown(agent)
        return base.rstrip("/") + path

    async def _request(self, agent: str, method: str, path: str, **kw) -> httpx.Response:
        try:
            resp = await self._http.request(method, self._url(agent, path), **kw)
        except httpx.TimeoutException as e:
            raise AgentTimeout(agent) from e
        except httpx.HTTPError as e:
            raise AgentDown(agent) from e
        if resp.is_success:
            return resp
        try:
            err = ErrorResponse.model_validate(resp.json()).error
        except (ValueError, ValidationError):
            raise AgentDown(f"{agent}: HTTP {resp.status_code}") from None
        if err.code == "timeout":
            raise AgentTimeout(agent)
        raise AgentError(err.code, err.message)

    async def turn(self, agent: str, req: TurnRequest) -> TurnResponse:
        resp = await self._request(
            agent,
            "POST",
            "/turn",
            content=req.model_dump_json(),
            headers={"content-type": "application/json"},
        )
        return _parse(agent, TurnResponse, resp)

    async def health(self, agent: str) -> Health:
        resp = await self._request(agent, "GET", "/health")
        return _parse(agent, Health, resp)


def _parse[M: BaseModel](agent: str, model: type[M], resp: httpx.Response) -> M:
    """A 2xx whose body is not the contract's shape means the agent is broken: treat it as down."""
    try:
        return model.model_validate(resp.json())
    except (ValueError, ValidationError):
        raise AgentDown(f"{agent}: malformed {model.__name__} body") from None
