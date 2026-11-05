from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    owner_number: str
    owner_uuid: str | None
    bot_number: str
    signal_url: str
    agents: dict[str, str]
    default_agent: str
    db_path: Path
    turn_timeout_s: float = 120
    split_limit: int = 1500
    tz: str = "America/New_York"


def _parse_agents(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        name, _, url = item.partition("=")
        out[name.strip()] = url.strip()
    return out


def load_settings(env: Mapping[str, str]) -> Settings:
    agents = _parse_agents(env["AGENTS"])
    default_agent = env["DEFAULT_AGENT"]
    if default_agent not in agents:
        raise ValueError(f"DEFAULT_AGENT {default_agent!r} is not in AGENTS {sorted(agents)}")
    return Settings(
        owner_number=env["OWNER_NUMBER"],
        owner_uuid=env.get("OWNER_UUID") or None,
        bot_number=env["BOT_NUMBER"],
        signal_url=env["SIGNAL_URL"],
        agents=agents,
        default_agent=default_agent,
        db_path=Path(env.get("DB_PATH", "data/gateway.db")),
    )
