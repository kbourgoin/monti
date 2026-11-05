import asyncio
import logging
import os

from .agent_client import HttpAgentClient
from .config import Settings, load_settings
from .router import Router, is_owner
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
        inb = parse_envelope(raw)
        if inb is None:
            return
        if inb.is_group or not is_owner(inb, self.s):
            log.debug("dropping message from non-owner or group")
            return
        recipient = self.s.owner_number
        async with self._locks.setdefault(recipient, asyncio.Lock()):
            await self.signal.typing(recipient, True)
            try:
                for part in await self.router.handle(inb):
                    await self.signal.send(recipient, part)
            except Exception:
                log.exception("failed handling inbound message")
            finally:
                await self.signal.typing(recipient, False)


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    settings = load_settings(os.environ)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    router = Router(
        settings,
        Store(settings.db_path),
        HttpAgentClient(settings.agents, settings.turn_timeout_s),
    )
    gateway = Gateway(settings, SignalClient(settings.signal_url, settings.bot_number), router)
    asyncio.run(gateway.run())
