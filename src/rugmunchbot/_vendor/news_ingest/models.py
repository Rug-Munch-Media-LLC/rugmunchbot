# VENDORED 2026-08-17 from rmi-backend/app/domains/news_ingest/models.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Data models for Telegram channel news ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(slots=True)
class ChannelMessage:
    """A single message read from a Telegram channel.

    Attributes:
        channel: Display name of the channel (title), if known.
        channel_username: Public @username without the leading ``@``.
        message_id: Telegram message id (unique within the channel).
        text: Message text (empty for media-only posts).
        media_type: ``text``, ``photo``, ``video``, ``document``, ... or
            ``media`` when the exact type is unknown.
        posted_at: Message send time (aware datetime when available).
        forwarded_from: Human/peer id the message was forwarded from, if any.
        views: Post view count when the channel exposes it.
        reactions: Emoji/document-id → count map when present.
    """

    channel: str
    channel_username: str
    message_id: int
    text: str = ""
    media_type: str = "text"
    posted_at: datetime | None = None
    forwarded_from: str | None = None
    views: int | None = None
    reactions: dict[str, int] | None = None


@dataclass(slots=True)
class NewsArticle:
    """A normalized news item derived from a channel message.

    Attributes:
        title: First line of the cleaned message (truncated to 120 chars).
        body: Full cleaned message text.
        source_channel: Channel name / username the article came from.
        source_url: Canonical source URL — the article's real URL when the
            message carries one (inline link / embedded preview / text URL),
            else the ``https://t.me/<username>/<message_id>`` fallback.
        category: News category (from the source config).
        posted_at: Original message time.
        content_hash: SHA-256 content fingerprint (``app.rag.dedup.content_hash``).
        entities: Extracted crypto entities ($SYMBOL, EVM, Solana addresses).
        media: Best-effort story image reference — a media id/url when the
            message carries photo/video media, or the source page's og:image
            URL (populated by the sweep; may be ``None``).
        resolved_url: Real article URL obtained by dereferencing a ``t.me``
            link when the message's only link was a Telegram page (populated
            by the sweep when ``RMI_NEWS_TME_RESOLVE_ENABLED``; ``None``
            otherwise). Never part of dedup keys — the stable ``source_url``
            stays authoritative for dedup/persistence.
        post_title: LLM-rewritten headline for the **posted** card (populated
            by the sweep when ``RMI_NEWS_TITLE_REWRITE_ENABLED``; ``None``
            otherwise). ``title`` keeps the original headline so persistence
            is never changed.
    """

    title: str
    body: str
    source_channel: str
    source_url: str
    category: str = ""
    posted_at: datetime | None = None
    content_hash: str = ""
    entities: list[str] = field(default_factory=list)
    media: str | None = None
    resolved_url: str | None = None
    post_title: str | None = None
