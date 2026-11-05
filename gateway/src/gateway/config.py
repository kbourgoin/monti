import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

_NUMBER = re.compile(r"^\+[1-9]\d{6,14}$")


@dataclass(frozen=True)
class User:
    name: str
    number: str
    uuid: str | None = None


@dataclass(frozen=True)
class Settings:
    users: tuple[User, ...]
    bot_number: str
    signal_url: str
    agents: dict[str, str]
    default_agent: str
    db_path: Path
    turn_timeout_s: float = 120
    split_limit: int = 1500
    tz: str = "America/New_York"
    bot_uuid: str | None = None


def _parse_agents(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        name, _, url = item.partition("=")
        out[name.strip()] = url.strip()
    return out


def parse_users(raw: str) -> tuple[User, ...]:
    users: list[User] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        name, eq, rest = item.partition("=")
        name = name.strip()
        number, slash, uuid = rest.strip().partition("/")
        number, uuid = number.strip(), uuid.strip()
        if not eq or not name or not _NUMBER.match(number) or (slash and not uuid):
            raise ValueError(f"malformed USERS entry {item!r}; expected Name=+E164[/uuid]")
        users.append(User(name, number, uuid.lower() or None))
    if not users:
        raise ValueError("USERS is empty; expected Name=+E164[/uuid],...")
    names = [u.name for u in users]
    numbers = [u.number for u in users]
    if len(set(names)) != len(names):
        raise ValueError("duplicate name in USERS")
    if len(set(numbers)) != len(numbers):
        raise ValueError("duplicate number in USERS")
    return tuple(users)


def load_settings(env: Mapping[str, str]) -> Settings:
    agents = _parse_agents(env["AGENTS"])
    default_agent = env["DEFAULT_AGENT"]
    if default_agent not in agents:
        raise ValueError(f"DEFAULT_AGENT {default_agent!r} is not in AGENTS {sorted(agents)}")
    return Settings(
        users=parse_users(env.get("USERS", "")),
        bot_number=env["BOT_NUMBER"],
        signal_url=env["SIGNAL_URL"],
        agents=agents,
        default_agent=default_agent,
        db_path=Path(env.get("DB_PATH", "data/gateway/gateway.db")),
        bot_uuid=env.get("BOT_UUID", "").strip().lower() or None,
    )
