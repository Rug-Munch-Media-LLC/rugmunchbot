"""Story ingestion adapter — OSS fail-open.

The private monolith (``app.rag.story_ingest``) fetches + RAG-ingests any
URL. OSS returns a fail-open ``FetchResult`` with ``ok=False``; the caller
(tools/story_fetch.py) already renders that as a clean error ToolResult.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FetchResult:
    ok: bool = False
    url: str = ""
    title: str = ""
    author: str = ""
    text: str = ""
    published_at: str | None = None
    source: str | None = None
    chain: str = ""
    error: str = "story fetch layer unavailable (OSS build)"
    data: dict[str, Any] = field(default_factory=dict)


async def fetch_story(url: str, timeout: float = 14.0) -> FetchResult:
    """Fail-open: report unavailable without touching the network."""
    return FetchResult(url=url)


async def ingest_url(url: str) -> dict[str, Any] | None:
    """Fail-open: no RAG ingestion in OSS."""
    return {"status": "skipped", "reason": "oss-build"}


__all__ = ["FetchResult", "fetch_story", "ingest_url"]
