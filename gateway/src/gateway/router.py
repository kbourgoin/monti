import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from turn_contract import TurnRequest

from .agent_client import AgentDown, AgentError, AgentPort, AgentTimeout
from .commands import Command, parse_command
from .config import Settings
from .formatting import split_reply
from .store import Store, TurnRecord

TIMEOUT_COPY = "That took too long. Try again or /new."
AUTH_COPY = "Monarch login expired. Run `mise run monarch-login` on the Mac."
HELP = (
    "Commands:\n"
    "/agent <name> - switch agent\n"
    "/new - start a fresh session with the current agent\n"
    "/which - show current agent, session and model\n"
    "/help - show this message"
)


@dataclass
class Inbound:
    sender_number: str | None
    sender_uuid: str | None
    text: str
    timestamp_ms: int
    is_group: bool


def is_owner(inb: Inbound, s: Settings) -> bool:
    if inb.sender_number is not None and inb.sender_number == s.owner_number:
        return True
    return s.owner_uuid is not None and inb.sender_uuid == s.owner_uuid


class Router:
    def __init__(self, settings: Settings, store: Store, agents: AgentPort):
        self.s = settings
        self.store = store
        self.agents = agents

    @property
    def _owner(self) -> str:
        return self.s.owner_number

    def _active(self) -> str:
        agent = self.store.active_agent(self._owner)
        return agent if agent in self.s.agents else self.s.default_agent

    async def handle(self, inb: Inbound) -> list[str]:
        if inb.is_group or not is_owner(inb, self.s):
            return []
        cmd = parse_command(inb.text)
        if cmd:
            return [await self._command(cmd)]
        return await self._turn(inb.text)

    async def _command(self, cmd: Command) -> str:
        agent = self._active()
        if cmd.name == "help":
            return HELP
        if cmd.name == "new":
            self.store.rotate_session(self._owner, agent)
            return f"Started a new session with {agent}."
        if cmd.name == "which":
            sid = self.store.current_session(self._owner, agent)
            try:
                model = (await self.agents.health(agent)).model
                detail = f"model {model}"
            except (AgentDown, AgentTimeout):
                detail = "model unavailable"
            return f"Agent: {agent}\nSession: {sid}\n{detail}"
        # agent
        choices = ", ".join(sorted(self.s.agents))
        if cmd.arg is None or cmd.arg not in self.s.agents:
            return f"{cmd.arg or '(none)'} is not a valid agent. Choose one of: {choices}."
        self.store.set_active_agent(self._owner, cmd.arg)
        return f"Switched to {cmd.arg}."

    async def _turn(self, text: str) -> list[str]:
        agent = self._active()
        sid = self.store.current_session(self._owner, agent)
        tz = ZoneInfo(self.s.tz)
        req = TurnRequest(
            session_id=sid,
            message_id=str(uuid.uuid4()),
            text=text,
            sender_name=self._owner,  # Task 3 replaces with the real name
            now=datetime.now(tz).replace(microsecond=0),
        )
        started = datetime.now(tz).isoformat()
        reply: str | None = None
        tool_calls: list[dict] = []
        in_tok = out_tok = 0
        try:
            resp = await self.agents.turn(agent, req)
        except AgentTimeout:
            status, out = "timeout", [TIMEOUT_COPY]
        except AgentDown:
            status, out = "error", [f"The {agent} agent is down."]
        except AgentError as e:
            status = "timeout" if e.code == "timeout" else "error"
            if e.code == "timeout":
                out = [TIMEOUT_COPY]
            elif e.code == "monarch_auth":
                out = [AUTH_COPY]
            else:
                out = [f"The {agent} agent is down."]
        else:
            status = "ok"
            reply = resp.reply
            tool_calls = [c.model_dump() for c in resp.tool_calls]
            in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
            out = split_reply(resp.reply, self.s.split_limit)
        self.store.log_turn(
            TurnRecord(
                session_id=sid, agent=agent, inbound=text, reply=reply, started_at=started,
                finished_at=datetime.now(tz).isoformat(), status=status,
                tool_calls=tool_calls, input_tokens=in_tok, output_tokens=out_tok,
            )
        )
        return out
