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
    login surfaces as MonarchAuthRequired (after one forced token refresh).

    A 401 inside an open session never reaches the caller as an auth error: the SDK's OAuth flow
    raises inside the transport's writer task, which tears the session down, and the pending
    request resolves as a CONNECTION_CLOSED `MCPError`. The session owner task keeps the
    teardown exception so `_run` can tell an auth failure from a plain dropped connection."""

    def __init__(self, storage: FileTokenStorage, *, url: str = MONARCH_MCP_URL) -> None:
        self._storage = storage
        self._url = url
        self._provider = make_provider(storage, interactive=False, server_url=url)
        self._session: ClientSession | None = None
        self._lock = asyncio.Lock()
        self._runner: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ended_with: BaseException | None = None

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
        if await self._storage.get_tokens() is None:
            # No login: stay fully offline. Registration/authorization is the login CLI's job.
            raise MonarchAuthRequired("No stored Monarch tokens")
        ready: asyncio.Future[ClientSession] = asyncio.get_running_loop().create_future()
        self._stop = stop = asyncio.Event()
        self._ended_with = None

        async def own() -> None:
            try:
                async with (
                    create_mcp_http_client(auth=self._provider) as http,
                    streamable_http_client(self._url, http_client=http) as streams,
                    ClientSession(*streams) as session,
                ):
                    await session.initialize()
                    ready.set_result(session)
                    await stop.wait()
            except BaseException as exc:  # noqa: BLE001 - relay everything to the awaiting caller
                if not ready.done():
                    ready.set_exception(exc)
                elif not isinstance(exc, asyncio.CancelledError):
                    self._ended_with = exc
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

    async def _disconnect(self) -> BaseException | None:
        """Stop the session; return the exception it ended with on its own, if any."""
        runner, self._runner, self._session = self._runner, None, None
        if runner is None:
            return None
        self._stop.set()
        try:
            await asyncio.wait_for(runner, timeout=5)
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
            log.debug("upstream close: %r", exc)
        ended, self._ended_with = self._ended_with, None
        return ended

    async def _run[T](self, op) -> T:
        async def attempt() -> T:
            async with self._lock:
                try:
                    return await op(await self._connect())
                except MCPError as exc:
                    if exc.code != types.CONNECTION_CLOSED:
                        raise  # a JSON-RPC error from Monarch itself
                    ended = await self._disconnect()
                    if ended is not None and is_auth_required(ended):
                        raise MonarchAuthRequired("Monarch rejected the access token") from ended
                    log.info("upstream session closed (%r); reconnecting once", ended)
                    return await op(await self._connect())
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
