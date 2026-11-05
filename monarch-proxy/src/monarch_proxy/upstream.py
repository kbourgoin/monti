"""Upstream access to Monarch's MCP server (or a fake, in tests)."""

import asyncio
import logging
from typing import Protocol, Self

from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import MCPError

from monarch_proxy.auth import (
    MONARCH_MCP_URL,
    MonarchAuthRequired,
    is_auth_required,
    make_provider,
    with_refresh_retry,
)
from monarch_proxy.tokens import FileTokenStorage

log = logging.getLogger("monarch_proxy")


class Upstream(Protocol):
    async def list_tools(self) -> list[types.Tool]: ...

    async def call_tool(self, name: str, args: dict) -> types.CallToolResult: ...


class MonarchUpstream:
    """One long-lived upstream session. Transport errors reconnect once and retry; an unusable
    login surfaces as MonarchAuthRequired (after one forced token refresh)."""

    def __init__(self, storage: FileTokenStorage) -> None:
        self._provider = make_provider(storage, interactive=False)
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
        self._runner: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def __aenter__(self) -> Self:
        try:
            async with self._lock:
                await self._connect()
        except Exception as exc:
            if not is_auth_required(exc):
                raise
            log.warning("Monarch login unusable at startup; run `make monarch-login`")
        return self

    async def __aexit__(self, *exc_info) -> None:
        async with self._lock:
            await self._disconnect()

    async def _connect(self) -> ClientSession:
        # The session's anyio scopes must be entered and exited by one task, but requests arrive
        # on many tasks, so a dedicated task owns the connection for its whole life.
        if self._session is not None:
            return self._session
        ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
        self._stop = stop = asyncio.Event()

        async def own() -> None:
            try:
                async with (
                    create_mcp_http_client(auth=self._provider) as http,
                    streamable_http_client(MONARCH_MCP_URL, http_client=http) as streams,
                    ClientSession(*streams) as session,
                ):
                    await session.initialize()
                    ready.set_result(session)
                    await stop.wait()
            except BaseException as exc:  # noqa: BLE001 - relay everything to the awaiting caller
                if not ready.done():
                    ready.set_exception(exc)
                elif not isinstance(exc, asyncio.CancelledError):
                    log.debug("upstream session ended: %r", exc)
            finally:
                self._session = None

        self._runner = asyncio.create_task(own())
        try:
            self._session = await ready
        except BaseException:
            await self._disconnect()
            raise
        return self._session

    async def _disconnect(self) -> None:
        runner, self._runner, self._session = self._runner, None, None
        if runner is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(runner, timeout=5)
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
            log.debug("upstream close: %r", exc)

    async def _run[T](self, op) -> T:
        async def attempt() -> T:
            async with self._lock:
                try:
                    return await op(await self._connect())
                except MCPError:
                    raise
                except Exception as exc:
                    await self._disconnect()
                    if is_auth_required(exc):
                        raise
                    log.info("upstream transport error (%r); reconnecting once", exc)
                    return await op(await self._connect())

        try:
            return await with_refresh_retry(self._provider, attempt)
        except MonarchAuthRequired:
            raise
        except BaseExceptionGroup as group:
            if is_auth_required(group):
                raise MonarchAuthRequired("Monarch login expired") from group
            raise

    async def list_tools(self) -> list[types.Tool]:
        return (await self._run(lambda s: s.list_tools())).tools

    async def call_tool(self, name: str, args: dict) -> types.CallToolResult:
        return await self._run(lambda s: s.call_tool(name, args))
