from datetime import datetime


def format_user_message(text: str, now: datetime, sender_name: str) -> str:
    """Prefix the user text with the current time and sender; system prompts stay static."""
    return f"Current time: {now.isoformat()}\nFrom: {sender_name}\n\n{text}"
