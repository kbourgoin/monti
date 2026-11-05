import asyncio
import base64
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx
import websockets

from .router import DirectRecipient, GroupRecipient, Inbound, Recipient

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MemberRef:
    number: str | None
    uuid: str | None


@dataclass
class GroupInfo:
    """A group as signal-cli lists it. `id` is the internal id, the same one receive envelopes carry."""

    id: str
    members: list[MemberRef]
    is_member: bool = False  # whether the bot itself is a full member (False while only invited)
    pending: list[MemberRef] = field(default_factory=list)  # invited or asking to join


@dataclass(frozen=True)
class GroupUpdate:
    """A group change (members, title, an invite to the bot...) sent by `sender`."""

    sender_number: str | None
    sender_uuid: str | None
    group_id: str


def rest_group_id(internal_id: str) -> str:
    """signal-cli-rest-api addresses a group as "group." + base64 of the internal id string."""
    return "group." + base64.b64encode(internal_id.encode()).decode()


def _str_or_none(v) -> str | None:
    return v if isinstance(v, str) and v else None


def _member(v) -> MemberRef:
    """Expanded listings give {number, uuid}; compact ones give a number, else a uuid.
    Anything unrecognisable becomes an empty ref, which the gate treats as an outsider."""
    if isinstance(v, dict):
        return MemberRef(_str_or_none(v.get("number")), _str_or_none(v.get("uuid")))
    if isinstance(v, str) and v:
        return MemberRef(v, None) if v.startswith("+") else MemberRef(None, v)
    return MemberRef(None, None)


def _members(v) -> list[MemberRef]:
    return [_member(m) for m in v] if isinstance(v, list) else []


def _group_info(entry) -> GroupInfo | None:
    if not isinstance(entry, dict):
        return None
    gid = _str_or_none(entry.get("internal_id"))
    if gid is None:
        return None
    pending = _members(entry.get("pending_invites")) + _members(entry.get("pending_requests"))
    return GroupInfo(gid, _members(entry.get("members")), entry.get("member") is True, pending)


def parse_envelope(msg: dict) -> Inbound | None:
    """Parse a signal-cli-rest-api receive payload; only plain text data messages count."""
    envelope = msg.get("envelope")
    if not isinstance(envelope, dict):
        return None
    data = envelope.get("dataMessage")
    if not isinstance(data, dict):
        return None
    text = data.get("message")
    if not isinstance(text, str) or not text:
        return None
    group = data.get("groupInfo") or data.get("groupV2")
    group_id: str | None = None
    if group:
        # A group frame with no usable id is still a group (never a DM); the gate denies "".
        gid = group.get("groupId") or group.get("id") if isinstance(group, dict) else None
        group_id = gid if isinstance(gid, str) else ""
    return Inbound(
        sender_number=envelope.get("sourceNumber") or envelope.get("source"),
        sender_uuid=envelope.get("sourceUuid"),
        text=text,
        timestamp_ms=data.get("timestamp") or envelope.get("timestamp") or 0,
        group_id=group_id,
    )


def parse_group_update(msg) -> GroupUpdate | None:
    """A group-change frame: a data message with groupInfo.type == "UPDATE" and no text."""
    envelope = msg.get("envelope") if isinstance(msg, dict) else None
    data = envelope.get("dataMessage") if isinstance(envelope, dict) else None
    group = data.get("groupInfo") if isinstance(data, dict) else None
    if not isinstance(group, dict) or group.get("type") != "UPDATE" or data.get("message"):
        return None
    gid = _str_or_none(group.get("groupId"))
    if gid is None:
        return None
    return GroupUpdate(
        _str_or_none(envelope.get("sourceNumber") or envelope.get("source")),
        _str_or_none(envelope.get("sourceUuid")),
        gid,
    )


class SignalClient:
    def __init__(self, base_url: str, bot_number: str):
        self.base_url = base_url.rstrip("/")
        self.bot_number = bot_number
        self._http = httpx.AsyncClient(timeout=30)

    async def receive(self) -> AsyncIterator[dict]:
        ws_url = (
            self.base_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
            + f"/v1/receive/{self.bot_number}"
        )
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    backoff = 1.0
                    async for frame in ws:
                        try:
                            yield json.loads(frame)
                        except json.JSONDecodeError:
                            log.warning("dropping non-JSON frame from signal-cli")
            except (OSError, websockets.WebSocketException) as e:
                log.warning("signal receive disconnected (%s); retrying in %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    @staticmethod
    def _address(recipient: Recipient) -> str:
        if isinstance(recipient, DirectRecipient):
            return recipient.number
        if isinstance(recipient, GroupRecipient) and recipient.group_id:
            return rest_group_id(recipient.group_id)
        raise ValueError(f"cannot address {recipient!r}")

    async def send(self, recipient: Recipient, text: str) -> None:
        r = await self._http.post(
            f"{self.base_url}/v2/send",
            json={"message": text, "number": self.bot_number, "recipients": [self._address(recipient)]},
        )
        r.raise_for_status()

    async def list_groups(self) -> list[GroupInfo]:
        """Every group signal-cli knows for the bot. Raises on any HTTP or shape error."""
        r = await self._http.get(f"{self.base_url}/v1/groups/{self.bot_number}", params={"expand": "true"})
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, list):
            raise TypeError(f"unexpected group list ({type(body).__name__})")
        return [g for g in map(_group_info, body) if g is not None]

    async def join_group(self, group_id: str) -> None:
        """Accept a pending invite (signal-cli `updateGroup -g` under the hood)."""
        r = await self._http.post(f"{self.base_url}/v1/groups/{self.bot_number}/{rest_group_id(group_id)}/join")
        r.raise_for_status()

    async def typing(self, recipient: Recipient, on: bool) -> None:
        address = self._address(recipient)
        try:
            r = await self._http.request(
                "PUT" if on else "DELETE",
                f"{self.base_url}/v1/typing-indicator/{self.bot_number}",
                json={"recipient": address},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("typing indicator failed: %s", e)
