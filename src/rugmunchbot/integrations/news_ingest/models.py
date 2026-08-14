"""Minimal news models (fail-open).

Type-checking contract for the ported channels code. The live
``NewsArticle``/``ChannelMessage`` dataclasses live in the private monolith
with more fields; these stand-ins cover every field the OSS channels modules
touch, so mypy stays clean and the no-op ingest never produces them at
runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class ChannelMessage:
    """A raw Telegram channel message (OSS stand-in)."""

    id: int = 0
    message: str = ""
    date: datetime | None = None
    posted_at: datetime | None = None
    sender_id: int | None = None
    username: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class NewsArticle:
    """A normalized news article (OSS stand-in)."""

    title: str = ""
    body: str = ""
    url: str = ""
    source: str = ""
    source_channel: str = ""
    source_url: str = ""
    category: str = "crypto_news"
    posted_at: datetime | None = None
    entities: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


__all__ = ["ChannelMessage", "NewsArticle"]
