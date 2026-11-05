from datetime import datetime


def format_user_message(text: str, now: datetime) -> str:
    """Prefix the user text with the current time; system prompts stay static."""
    return f"Current time: {now.isoformat()}\n\n{text}"
