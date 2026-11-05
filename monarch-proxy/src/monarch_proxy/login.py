"""`monarch-login`: interactive OAuth login (or `--check`: non-interactive credential check)."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from monarch_proxy.auth import (
    MONARCH_MCP_URL,
    is_auth_required,
    make_provider,
    with_refresh_retry,
)
from monarch_proxy.tokens import FileTokenStorage

DEFAULT_TOKEN_PATH = Path("data/monarch/tokens.json")


async def count_tools(provider) -> int:
    async with (
        create_mcp_http_client(auth=provider) as http_client,
        streamable_http_client(MONARCH_MCP_URL, http_client=http_client) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        return len((await session.list_tools()).tools)


async def run(token_path: Path, *, check: bool) -> int:
    provider = make_provider(FileTokenStorage(token_path), interactive=not check)
    if check:
        n = await with_refresh_retry(provider, lambda: count_tools(provider))
        print(f"Credentials OK. {n} tools upstream.")
    else:
        n = await count_tools(provider)
        print(f"Logged in. {n} tools upstream.")
    return n


def main() -> None:
    parser = argparse.ArgumentParser(prog="monarch-login")
    parser.add_argument("--check", action="store_true", help="non-interactive credential check")
    parser.add_argument(
        "--tokens", type=Path, default=Path(os.environ.get("MONARCH_TOKENS", DEFAULT_TOKEN_PATH))
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args.tokens, check=args.check))
    except BaseException as exc:
        if not is_auth_required(exc):
            raise
        print("Monarch login expired. Run `make monarch-login` on the Mac.", file=sys.stderr)
        sys.exit(1)
