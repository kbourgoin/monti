"""Append-only JSONL audit log of tool calls. Never records result bodies."""

import json
from datetime import UTC, datetime
from pathlib import Path


class CallLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, *, tool: str, args: dict, ms: int, ok: bool, result_bytes: int) -> None:
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "tool": tool,
            "args": args,
            "ms": ms,
            "ok": ok,
            "result_bytes": result_bytes,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
