"""Story-level RAG ingestion loop for the TG news rail.

Split out of ``telegram_news/_rag.py`` (200-line gate). Owns the bounded-
concurrency per-story ingest and the report -> counter mapping. Failure counts
are returned, never swallowed: the caller turns them into a ``degraded`` marker.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.tg_news")

#: Max concurrent per-story RAG ingests. Each story embeds + upserts chunks
#: over the network; bounded concurrency collapses N serial stories into
#: ~N/3 wall-time without overloading the embedder (whose client caps at 8).
STORY_INGEST_CONCURRENCY: int = 3


def _count_report(report: Any, counts: dict[str, Any]) -> None:
    """Fold one ingest report into the counter dict (loud on real errors)."""
    if report.accepted:
        counts["ingested"] += 1
        return
    if report.duplicate:
        counts["duplicates"] += 1
        return
    if getattr(report, "rejected", 0):
        # Quality gate rejection is EXPECTED, not a pipeline error.
        counts["rejected_gate"] = counts.get("rejected_gate", 0) + 1
        return
    counts["errors"] += len(report.errors or []) or 1
    logger.warning(
        "tg_news:rag_story_error status=%s errs=%s",
        getattr(report, "status", "?"),
        (report.errors or ["?"])[:2],
    )
    reasons = getattr(report, "rejections", None) or {}
    if isinstance(reasons, dict) and reasons:
        top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
        logger.info("tg_news:rag_reject_reasons %s", " ".join(f"{k}={v}" for k, v in top))


async def _ingest_one(
    ingestor: Any, doc: dict[str, Any], image_by_url: dict[str, str], collection: str
) -> Any:
    """Ingest a single story doc (image backfill included)."""
    metadata = dict(doc.get("metadata") or {})
    if not metadata.get("image_url"):
        for member_url in metadata.get("source_urls") or []:
            if image_by_url.get(member_url):
                metadata["image_url"] = image_by_url[member_url]
                break
    doc["metadata"] = metadata
    return await ingestor.ingest(
        content=doc["content"],
        collection=collection,
        doc_id=doc["doc_id"],
        metadata=doc["metadata"],
        source_type="news",
    )


async def ingest_story_docs(
    story_docs: list[dict[str, Any]], image_by_url: dict[str, str], collection: str
) -> dict[str, Any]:
    """Ingest each story doc concurrently; return ``ingested/duplicates/errors``."""
    counts: dict[str, Any] = {"ingested": 0, "duplicates": 0, "errors": 0, "rejected_gate": 0}
    if not story_docs:
        return counts
    from rugmunchbot._vendor.rag_pkg.ingestion import BatchIngestor

    ingestor = BatchIngestor()
    sem = asyncio.Semaphore(STORY_INGEST_CONCURRENCY)

    async def _bounded(doc: dict[str, Any]) -> Any:
        async with sem:
            return await _ingest_one(ingestor, doc, image_by_url, collection)

    reports = await asyncio.gather(*(_bounded(d) for d in story_docs), return_exceptions=True)
    for report in reports:
        if isinstance(report, BaseException):
            counts["errors"] += 1
            logger.warning(
                "tg_news:rag_story_failed err=%s", f"{type(report).__name__}:{str(report)[:140]}"
            )
            continue
        _count_report(report, counts)
    return counts


__all__ = ["STORY_INGEST_CONCURRENCY", "ingest_story_docs"]
