"""Channel-message → article normalization — OSS fail-open no-op."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot.integrations.news_ingest.models import NewsArticle


def channel_message_to_article(message: Any, category: str = "crypto_news") -> NewsArticle | None:
    """No-op: OSS has no normalization pipeline, returns ``None``."""
    return None


__all__ = ["channel_message_to_article"]
