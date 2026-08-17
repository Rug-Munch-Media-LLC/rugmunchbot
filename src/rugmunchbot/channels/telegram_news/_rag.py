"""TG news → RAG ingestion (additive, fail-open).

Submodule of ``channels/telegram_news.py`` — owns the **feed-RAG** hop of the
pipeline: freshly-swept TG articles are clustered into stories and embedded
into the RAG ``news_v2`` collection (the collection the bot's ``get_news``
RAG tool and the news router search), so RAG "benefits from" the news lane.

The path mirrors the databus nightly indexer
(``app.databus.rag_ingestion.index_databus_news``):

    NewsArticle → clusterer NewsItem → build_story_rag_docs (ONE RAG doc per
    story, stable ``doc_id`` = ``news:{cluster_id}``) → BatchIngestor
    (quality filter → rate limiter → chunk → embed) into ``news_v2``.

Contract notes:
* Every swept article is fed to RAG (store everything, embed everything) —
  the importance rerank only decides what gets *posted* to Telegram, never
  what RAG learns about. Story clustering still dedupes the same story that
  arrived from multiple channels into a single RAG doc.
* Idempotent: ``build_story_rag_docs`` produces the same ``doc_id`` for the
  same story, and the engine's chunk-hash dedup (``rag:hashes:news_v2``)
  skips already-embedded chunks — re-running a sweep never double-embeds.
* Fail-open: any RAG outage (embedder, Qdrant, rate limit) logs and returns
  counts with ``errors``; the website + Telegram halves of the pipeline keep
  working. ``TG_NEWS_RAG`` (env ``RMI_TG_NEWS_RAG``) is read through the shim
  at call time so ``patch.object(tg_news, "TG_NEWS_RAG", ...)`` keeps working.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.domains.news_ingest.ingest import extract_canonical_url, extract_image_url
from app.domains.telegram.rugmunchbot.channels.telegram_news._persist import _news_id_for
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from app.domains.news_ingest.models import NewsArticle
    from app.domains.telegram.rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")

#: RAG collection the news lane feeds — the collection ``get_news`` (RAG
#: tool) and the news router search (mirrors ``index_databus_news``).
_RAG_NEWS_COLLECTION: str = "news_v2"

#: Max concurrent per-story RAG ingests. Each story embeds + upserts chunks
#: over the network; bounded concurrency collapses N serial stories into
#: ~N/3 wall-time without overloading the embedder (whose own client caps
#: at 8 connections).
_STORY_INGEST_CONCURRENCY: int = 3


async def ingest_into_rag(articles: list[NewsArticle]) -> dict[str, Any]:
    """Embed freshly-swept TG articles into the RAG ``news_v2`` collection.

    Returns ``{"stories", "ingested", "duplicates", "errors"}``. Fails open:
    a disabled gate, empty input, or a RAG outage all return counts without
    raising — the scheduler keeps running and the other halves of the
    pipeline still work.
    """
    counts: dict[str, Any] = {"stories": 0, "ingested": 0, "duplicates": 0, "errors": 0}
    if not articles:
        return counts
    if not _tg.TG_NEWS_RAG:
        logger.info("tg_news:rag_ingest_disabled articles=%s", len(articles))
        return counts
    try:
        from app.domains.news.clusterer import (
            NewsItem as ClusterNewsItem,
            build_story_rag_docs,
        )
        from app.rag.ingestion import BatchIngestor

        cluster_input = [
            ClusterNewsItem(
                id=_news_id_for(article),
                title=article.title or "",
                body=article.body or "",
                source=article.source_channel or "",
                url=extract_canonical_url(article) or article.source_url,
                published_at=article.posted_at or datetime.now(UTC),
                sentiment=0.0,  # the clusterer derives the rating card itself
            )
            for article in articles
        ]
        story_docs = build_story_rag_docs(cluster_input)
        counts["stories"] = len(story_docs)
        # Enrich each story doc with an image URL when one of its member
        # articles carried one (inline image link or the sweep-populated
        # ``media`` field; the website's ``NewsArticle.image_url`` field reads
        # it from Qdrant metadata). Key the map by BOTH the raw channel URL
        # and the canonical source URL: story ``source_urls`` hold the
        # canonical link (``extract_canonical_url`` in the cluster input), so
        # a lookup keyed only on ``article.source_url`` would miss.
        image_by_url: dict[str, str] = {}
        for article in articles:
            image = extract_image_url(article) or getattr(article, "media", None) or ""
            if not image:
                continue
            image_by_url[article.source_url] = image
            canonical = extract_canonical_url(article)
            if canonical:
                image_by_url[canonical] = image
        ingestor = BatchIngestor()
        # Story ingestion is independent and network-bound (embed + Qdrant
        # upsert per chunk), so run the per-story ingest concurrently with a
        # bounded semaphore instead of serializing N stories. Keeps the
        # per-story fail-open contract; the count dict is updated by the
        # single task loop after all stories finish.
        sem = asyncio.Semaphore(_STORY_INGEST_CONCURRENCY)

        async def _ingest_story(doc: dict[str, Any]) -> Any:
            async with sem:
                metadata = dict(doc.get("metadata") or {})
                if not metadata.get("image_url"):
                    for member_url in metadata.get("source_urls") or []:
                        if image_by_url.get(member_url):
                            metadata["image_url"] = image_by_url[member_url]
                            break
                doc["metadata"] = metadata
                report = await ingestor.ingest(
                    content=doc["content"],
                    collection=_RAG_NEWS_COLLECTION,
                    doc_id=doc["doc_id"],
                    metadata=doc["metadata"],
                    source_type="news",
                )
                return report

        reports = await asyncio.gather(*(_ingest_story(doc) for doc in story_docs), return_exceptions=True)
        for report in reports:
            if isinstance(report, BaseException):
                counts["errors"] += 1
                logger.debug("tg_news:rag_story_failed err=%s", str(report)[:160])
                continue
            if report.accepted:
                counts["ingested"] += 1
            elif report.duplicate:
                counts["duplicates"] += 1
            else:
                counts["errors"] += len(report.errors or []) or 1
        logger.info(
            "tg_news:rag_ingested articles=%s stories=%s ingested=%s duplicates=%s errors=%s",
            len(articles),
            counts["stories"],
            counts["ingested"],
            counts["duplicates"],
            counts["errors"],
        )
    except Exception as exc:  # fail-open: a RAG outage never crashes the job
        logger.warning("tg_news:rag_ingest_failed err=%s", str(exc)[:200])
        counts["errors"] = len(articles)
    return counts
