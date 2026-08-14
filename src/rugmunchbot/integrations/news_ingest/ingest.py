"""News ingestion helpers — OSS fail-open no-ops.

Private behavior (rmi-backend): read channel messages via the headless
Telethon client, dedupe by content hash, mark seen in Redis. OSS default:
read nothing, keep everything, mark nothing.
"""

from __future__ import annotations

from typing import Any

DEFAULT_DEDUP_COLLECTION = "oss:dedup:none"


async def read_channel_messages(client: Any, username: str, limit: int = 30) -> list[Any]:
    """No-op: return ``[]`` (no headless client in OSS)."""
    return []


def dedupe_articles(articles: list[Any]) -> list[Any]:
    """Pass-through dedupe (single-batch: nothing to dedupe against)."""
    return articles


def filter_seen_articles(
    articles: list[Any], collection: str = DEFAULT_DEDUP_COLLECTION
) -> tuple[list[Any], int]:
    """No-op: nothing is "seen", keep everything, skip 0."""
    return articles, 0


def mark_articles_ingested(articles: list[Any], collection: str = DEFAULT_DEDUP_COLLECTION) -> None:
    """No-op: OSS does not persist a seen-set to Redis."""
    return None


__all__ = [
    "DEFAULT_DEDUP_COLLECTION",
    "dedupe_articles",
    "filter_seen_articles",
    "mark_articles_ingested",
    "read_channel_messages",
]
