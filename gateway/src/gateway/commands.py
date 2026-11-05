from dataclasses import dataclass
from typing import Literal

_NAMES = ("agent", "new", "which", "help")


@dataclass(frozen=True)
class Command:
    name: Literal["agent", "new", "which", "help"]
    arg: str | None


def parse_command(text: str) -> Command | None:
    parts = text.strip().lower().split()
    if not parts or not parts[0].startswith("/"):
        return None
    name = parts[0][1:]
    if name not in _NAMES:
        return None
    return Command(name, parts[1] if len(parts) > 1 else None)  # type: ignore[arg-type]
