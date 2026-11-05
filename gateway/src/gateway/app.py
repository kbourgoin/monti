import asyncio
import logging
import os
from collections.abc import Awaitable, Callable

from .agent_client import HttpAgentClient
from .config import Settings, load_settings
from .groups import GroupEvents, MembershipGate
from .router import GroupRecipient, Inbound, Recipient, Router, chat_id, match_user
from .signal_client import SignalClient, parse_envelope, parse_group_update
from .store import Store

log = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class Gateway:
    """Receives Signal frames and hands each one to its chat's queue.

    The receive loop never awaits a turn. Each chat (`dm:<number>` / `group:<id>`) has
    one queue and at most one worker, so a chat's messages run one at a time in arrival
    order while different chats run concurrently. Workers start on demand and exit
    after `idle_s` without work.
    """

    def __init__(
        self,
        settings: Settings,
        signal,
        router: Router,
        groups: GroupEvents | None = None,
        idle_s: float = 60.0,
    ):
        self.s = settings
        self.signal = signal
        self.router = router
        self.groups = groups
        self.idle_s = idle_s
        self._queues: dict[str, asyncio.Queue[Job]] = {}
        self._workers: dict[str, asyncio.Task] = {}

    async def run(self) -> None:
        async for raw in self.signal.receive():
            self.submit(raw)
        await self.drain()  # only reached when the stream ends (tests)

    def submit(self, raw) -> None:
        """Parse a frame and queue it for its chat. Never blocks."""
        try:
            update = parse_group_update(raw)
            inb = None if update is not None else parse_envelope(raw)
        except Exception:
            log.exception("skipping malformed Signal frame (%s)", type(raw).__name__)
            return
        if update is not None:
            if self.groups is not None:
                self.groups.gate.invalidate(update.group_id)  # now, not when the job runs
                groups = self.groups
                self._enqueue(f"group:{update.group_id}", lambda: groups.on_update(update))
            return
        if inb is None:
            return
        user = match_user(inb, self.s)
        if user is None:
            log.info(  # never the text
                "dropping message from unknown sender (number=%s uuid=%s)",
                inb.sender_number, inb.sender_uuid,
            )
            return  # the router would drop it too; strangers never get a worker
        self._enqueue(chat_id(inb, user), lambda: self.handle(inb))

    async def handle(self, inb: Inbound) -> None:
        """One message: typing on once admitted, the router, the replies, typing off. Never raises."""
        typing_to: Recipient | None = None

        async def start_typing(recipient: Recipient) -> None:
            nonlocal typing_to
            typing_to = recipient
            await self.signal.typing(recipient, True)

        try:
            reply = await self.router.handle(inb, start_typing)
            if reply is not None:
                if isinstance(reply.recipient, GroupRecipient) and not await self._still_allowed(
                    reply.recipient.group_id
                ):
                    return
                for part in reply.messages:
                    await self.signal.send(reply.recipient, part)
        except Exception:
            log.exception("failed handling inbound message")
        finally:
            if typing_to is not None:
                try:
                    await self.signal.typing(typing_to, False)
                except Exception:
                    log.warning("turning the typing indicator off failed", exc_info=True)

    async def _still_allowed(self, group_id: str) -> bool:
        """Membership may have changed during the turn; re-check before speaking."""
        try:
            if await self.router.gate.allowed(group_id):
                return True
        except Exception:
            log.exception("group re-check failed; dropping reply (fail-closed)")
            return False
        log.info("group %s no longer allowed; dropping reply", group_id)
        return False

    def _enqueue(self, key: str, job: Job) -> None:
        q = self._queues.setdefault(key, asyncio.Queue())
        q.put_nowait(job)
        worker = self._workers.get(key)
        if worker is None or worker.done():
            self._workers[key] = asyncio.create_task(self._work(key, q), name=f"chat {key}")

    async def _work(self, key: str, q: asyncio.Queue[Job]) -> None:
        me = asyncio.current_task()
        while True:
            try:
                job = await asyncio.wait_for(q.get(), self.idle_s)
            except TimeoutError:
                if not q.empty():
                    continue
                # No await between the check and the removal, so submit() can't slip in.
                self._queues.pop(key, None)
                if self._workers.get(key) is me:
                    del self._workers[key]
                return
            try:
                await job()
            except asyncio.CancelledError:
                if me is not None and me.cancelling():
                    raise  # real shutdown
                log.error("chat %s: a job was cancelled; continuing", key)
            except Exception:
                log.exception("chat %s: job failed; continuing", key)
            finally:
                q.task_done()

    async def drain(self) -> None:
        """Wait until every queued message has been handled."""
        await asyncio.gather(*(q.join() for q in list(self._queues.values())))

    def active_chats(self) -> set[str]:
        return {k for k, w in self._workers.items() if not w.done()}


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    settings = load_settings(os.environ)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    signal = SignalClient(settings.signal_url, settings.bot_number)
    gate = MembershipGate(signal, settings)
    router = Router(
        settings,
        Store(settings.db_path),
        HttpAgentClient(settings.agents, settings.turn_timeout_s),
        gate,
    )
    gateway = Gateway(settings, signal, router, GroupEvents(signal, settings, gate))
    asyncio.run(gateway.run())
