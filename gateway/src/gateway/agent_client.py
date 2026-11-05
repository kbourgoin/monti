from typing import Protocol

from turn_contract import Health, TurnRequest, TurnResponse


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
