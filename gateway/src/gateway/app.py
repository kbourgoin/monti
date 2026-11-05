import asyncio
import logging
import os

from .agent_client import HttpAgentClient
from .config import Settings, load_settings
from .router import Recipient, Router
from .signal_client import SignalClient, parse_envelope
from .store import Store

log = logging.getLogger(__name__)


class Gateway:
    def __init__(self, settings: Settings, signal, router: Router):
        self.s = settings
        self.signal = signal
        self.router = router
        self._locks: dict[str, asyncio.Lock] = {}

    async def run(self) -> None:
        async for raw in self.signal.receive():
            await self.on_message(raw)

    async def on_message(self, raw: dict) -> None:
        try:
            inb = parse_envelope(raw)
            if inb is None:
                return
        except Exception:
            log.exception("skipping malformed Signal frame (%s)", type(raw).__name__)
            return
        # Task 4 replaces this with per-chat workers.
        key = f"group:{inb.group_id}" if inb.group_id is not None else f"dm:{inb.sender_number or inb.sender_uuid}"
        typing_to: Recipient | None = None

        async def start_typing(recipient: Recipient) -> None:
            nonlocal typing_to
            typing_to = recipient
            await self.signal.typing(recipient, True)

        async with self._locks.setdefault(key, asyncio.Lock()):
            try:
                reply = await self.router.handle(inb, start_typing)
                if reply is not None:
                    for part in reply.messages:
                        await self.signal.send(reply.recipient, part)
            except Exception:
                log.exception("failed handling inbound message")
            finally:
                if typing_to is not None:
                    await self.signal.typing(typing_to, False)


class DenyAllGroups:
    """Fail-closed placeholder until Task 4's membership gate."""

    async def allowed(self, group_id: str) -> bool:
        return False


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    settings = load_settings(os.environ)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    router = Router(
        settings,
        Store(settings.db_path),
        HttpAgentClient(settings.agents, settings.turn_timeout_s),
        DenyAllGroups(),
    )
    gateway = Gateway(settings, SignalClient(settings.signal_url, settings.bot_number), router)
    asyncio.run(gateway.run())
