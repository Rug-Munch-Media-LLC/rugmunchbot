"""Website catalog adapter — OSS fail-open.

The live bot persists news to the website catalog (Postgres via
``app.catalog.*``). OSS ships a no-op ``get_catalog`` that reports Postgres
unavailable, so ``persist_articles`` degrades to "skipped" instead of
crashing the channel job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


class _Health:
    postgres: bool = False


class _CatalogStub:
    """Minimal stand-in: reports Postgres down and exposes no pool."""

    _health = _Health()
    _pg_pool: Any | None = None

    async def _init_stores(self) -> None:
        return None


def utcnow() -> datetime:
    return datetime.now(UTC)


class NewsItem:
    """Stand-in row shape (never persisted in OSS)."""

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


def get_catalog() -> Any:
    """Return a stub catalog with Postgres marked unavailable."""
    return _CatalogStub()


__all__ = ["NewsItem", "get_catalog", "utcnow"]
