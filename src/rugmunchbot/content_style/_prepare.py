"""The single pre-send entry point for every Telegram surface."""

from __future__ import annotations

from rugmunchbot.content_style._normalize import normalize
from rugmunchbot.content_style._rules import SAFE_LIMIT
from rugmunchbot.content_style._split import split_html

__all__ = ["prepare_for_send"]


def prepare_for_send(text: str, *, limit: int = SAFE_LIMIT, scrub_emoji: bool = False) -> list[str]:
    """Normalize then split *text* into sendable parts (never empty-parts)."""
    if not text or not text.strip():
        return []
    return [
        part for part in split_html(normalize(text, scrub_emoji=scrub_emoji), limit) if part.strip()
    ]
