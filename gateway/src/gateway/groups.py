"""Group admission: Monty speaks in a group only when everyone else in it is a user.

Fail-closed throughout: a failed or slow lookup, an unknown group, a member we can't
identify, or a group the bot isn't a full member of all mean "stay silent".
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Protocol

from .config import Settings, User
from .router import Inbound, match_user
from .signal_client import GroupInfo, GroupUpdate, MemberRef

log = logging.getLogger(__name__)


class GroupDirectory(Protocol):
    async def list_groups(self) -> list[GroupInfo]: ...


class GroupJoiner(GroupDirectory, Protocol):
    async def join_group(self, group_id: str) -> None: ...


def member_user(m: MemberRef, s: Settings) -> User | None:
    """A member is a user by number; by uuid only when signal-cli gave no number."""
    for u in s.users:
        if m.number is not None:
            if m.number == u.number:
                return u
        elif m.uuid is not None and m.uuid == u.uuid:
            return u
    return None


def is_bot(m: MemberRef, s: Settings) -> bool:
    return (m.number is not None and m.number == s.bot_number) or (
        s.bot_uuid is not None and m.uuid == s.bot_uuid
    )


class MembershipGate:
    """`GroupGate`: true only when every member (and pending member) other than the bot is a user."""

    def __init__(
        self,
        signal: GroupDirectory,
        settings: Settings,
        ttl_s: float = 60,
        timeout_s: float = 10,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.signal = signal
        self.s = settings
        self.ttl_s = ttl_s
        self.timeout_s = timeout_s
        self.clock = clock
        self._cache: dict[str, tuple[float, GroupInfo | None]] = {}

    def invalidate(self, group_id: str) -> None:
        self._cache.pop(group_id, None)

    async def _lookup(self, group_id: str) -> GroupInfo | None:
        now = self.clock()
        hit = self._cache.get(group_id)
        if hit is not None and now - hit[0] < self.ttl_s:
            return hit[1]
        fresh = {g.id: g for g in await asyncio.wait_for(self.signal.list_groups(), self.timeout_s)}
        for gid, g in fresh.items():
            self._cache[gid] = (now, g)
        self._cache[group_id] = (now, fresh.get(group_id))  # an unknown group's "no" is cached too
        return fresh.get(group_id)

    async def allowed(self, group_id: str) -> bool:
        if not group_id:
            return False
        try:
            group = await self._lookup(group_id)
        except Exception as e:
            # Timeouts included. Failures aren't cached, so the next message retries.
            log.warning(
                "group membership lookup failed for %s (%r); staying silent",
                group_id,
                e,
                exc_info=True,
            )
            return False
        if group is None:
            log.info("group %s not known to signal-cli; staying silent", group_id)
            return False
        if not group.is_member:
            log.info("bot is not a full member of group %s; staying silent", group_id)
            return False
        outsiders = [
            m
            for m in group.members + group.pending
            if not is_bot(m, self.s) and member_user(m, self.s) is None
        ]
        if outsiders:
            log.info(
                "group %s has %d member(s) not in USERS; staying silent", group_id, len(outsiders)
            )
            return False
        return True


class GroupEvents:
    """Handles group-change frames: refresh the gate, and accept invites sent by a user.

    signal-cli does not accept group invites on its own (`updateGroup -g` accepts one),
    so an invite from someone in USERS is accepted here. Invites from anyone else are
    left pending. Speaking is still decided by the gate either way.
    """

    def __init__(self, signal: GroupJoiner, settings: Settings, gate: MembershipGate):
        self.signal = signal
        self.s = settings
        self.gate = gate

    async def on_update(self, upd: GroupUpdate) -> None:
        self.gate.invalidate(upd.group_id)
        sender = match_user(
            Inbound(upd.sender_number, upd.sender_uuid, "", 0, upd.group_id), self.s
        )
        if sender is None:
            return
        try:
            groups = await self.signal.list_groups()
            group = next((g for g in groups if g.id == upd.group_id), None)
            if group is None or group.is_member:
                return
            await self.signal.join_group(upd.group_id)
            log.info("accepted group invite to %s from %s", upd.group_id, sender.name)
        except Exception as e:
            log.warning(
                "could not check or accept group invite for %s (%r)", upd.group_id, e, exc_info=True
            )
