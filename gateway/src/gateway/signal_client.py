import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx
import websockets

from .router import Inbound

log = logging.getLogger(__name__)


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
    return Inbound(
        sender_number=envelope.get("sourceNumber") or envelope.get("source"),
        sender_uuid=envelope.get("sourceUuid"),
        text=text,
        timestamp_ms=data.get("timestamp") or envelope.get("timestamp") or 0,
        is_group=bool(data.get("groupInfo") or data.get("groupV2")),
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

    async def send(self, recipient: str, text: str) -> None:
        r = await self._http.post(
            f"{self.base_url}/v2/send",
            json={"message": text, "number": self.bot_number, "recipients": [recipient]},
        )
        r.raise_for_status()

    async def typing(self, recipient: str, on: bool) -> None:
        try:
            r = await self._http.request(
                "PUT" if on else "DELETE",
                f"{self.base_url}/v1/typing-indicator/{self.bot_number}",
                json={"recipient": recipient},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("typing indicator failed: %s", e)
