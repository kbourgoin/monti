import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from turn_contract import TurnRequest

from .agent_client import AgentDown, AgentError, AgentPort, AgentTimeout
from .commands import Command, parse_command
from .config import Settings, User
from .formatting import split_reply
from .store import Store, TurnRecord

log = logging.getLogger(__name__)

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
    group_id: str | None = None

    @property
    def is_group(self) -> bool:
        return self.group_id is not None


@dataclass(frozen=True)
class DirectRecipient:
    address: str  # the person's uuid, or E.164 number when there's no uuid


@dataclass(frozen=True)
class GroupRecipient:
    group_id: str


Recipient = DirectRecipient | GroupRecipient


@dataclass
class Reply:
    chat_id: str
    recipient: Recipient
    messages: list[str]


class GroupGate(Protocol):
    async def allowed(self, group_id: str) -> bool: ...


def match_user(inb: Inbound, s: Settings) -> User | None:
    for u in s.users:
        if inb.sender_number is not None:
            if inb.sender_number == u.number:
                return u
        elif u.uuid is not None and inb.sender_uuid is not None:
            if inb.sender_uuid.lower() == u.uuid.lower():
                return u
    return None


def chat_id(inb: Inbound, user: User) -> str:
    return f"group:{inb.group_id}" if inb.group_id is not None else f"dm:{user.number}"


class Router:
    def __init__(self, settings: Settings, store: Store, agents: AgentPort, gate: GroupGate):
        self.s = settings
        self.store = store
        self.agents = agents
        self.gate = gate

    def _active(self, chat: str) -> str:
        agent = self.store.active_agent(chat)
        return agent if agent in self.s.agents else self.s.default_agent

    async def handle(
        self,
        inb: Inbound,
        on_admitted: Callable[[Recipient], Awaitable[None]] | None = None,
    ) -> Reply | None:
        """Admit, then answer. `on_admitted` runs once the sender (and group) pass, before the turn."""
        user = match_user(inb, self.s)
        if user is None:
            return None
        recipient: Recipient
        if inb.group_id is not None:
            try:
                if not await self.gate.allowed(inb.group_id):
                    return None
            except Exception:
                log.exception("group gate failed; dropping (fail-closed)")
                return None
            recipient = GroupRecipient(inb.group_id)
        else:
            # By uuid when known: signal-cli can't send to a number its owner keeps undiscoverable.
            recipient = DirectRecipient(inb.sender_uuid or user.number)
        if on_admitted is not None:
            await on_admitted(recipient)
        chat = chat_id(inb, user)
        cmd = parse_command(inb.text)
        messages = [await self._command(chat, cmd)] if cmd else await self._turn(chat, user, inb.text)
        return Reply(chat, recipient, messages)

    async def _command(self, chat: str, cmd: Command) -> str:
        agent = self._active(chat)
        if cmd.name == "help":
            return HELP
        if cmd.name == "new":
            self.store.rotate_session(chat, agent)
            return f"Started a new session with {agent}."
        if cmd.name == "which":
            sid = self.store.current_session(chat, agent)
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
        self.store.set_active_agent(chat, cmd.arg)
        return f"Switched to {cmd.arg}."

    async def _turn(self, chat: str, user: User, text: str) -> list[str]:
        agent = self._active(chat)
        sid = self.store.current_session(chat, agent)
        tz = ZoneInfo(self.s.tz)
        req = TurnRequest(
            session_id=sid,
            message_id=str(uuid.uuid4()),
            text=text,
            sender_name=user.name,
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
                chat_id=chat, sender_name=user.name, session_id=sid, agent=agent, inbound=text, reply=reply, started_at=started,
                finished_at=datetime.now(tz).isoformat(), status=status,
                tool_calls=tool_calls, input_tokens=in_tok, output_tokens=out_tok,
            )
        )
        return out
