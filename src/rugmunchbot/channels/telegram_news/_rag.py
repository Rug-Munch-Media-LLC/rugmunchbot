"""TG news → RAG ingestion (additive, fail-open, never silent).

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
* Visibility (2026-10-25): the returned counts always carry ``degraded`` and
  ``status`` (``ok`` / ``partial`` / ``failed`` / ``disabled``) so a partially
  embedded sweep can no longer look identical to a healthy one.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.news_ingest.ingest import extract_canonical_url
from rugmunchbot.channels.telegram_news._persist import _news_id_for
from rugmunchbot.channels.telegram_news._rag_stories import ingest_story_docs
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import NewsArticle
    from rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")

#: RAG collection the news lane feeds — the collection ``get_news`` (RAG
#: tool) and the news router search (mirrors ``index_databus_news``).
_RAG_NEWS_COLLECTION: str = "news_v2"

#: Minimum body length for a post to become a RAG story (see the gate below).
_MIN_BODY_CHARS: int = 60


def _status(counts: dict[str, Any]) -> str:
    """Derive the lane status from the counters (never a silent ``ok``)."""
    if counts.get("disabled"):
        return "disabled"
    if not counts["errors"]:
        return "ok" if counts["ingested"] else ("empty" if not counts["stories"] else "partial")
    return "partial" if counts["ingested"] else "failed"


def _build_story_docs(articles: list[NewsArticle]) -> list[dict[str, Any]]:
    """Cluster articles into story RAG docs (bodyless posts dropped first)."""
    from rugmunchbot._vendor.news_clusterer import (
        NewsItem as ClusterNewsItem,
        build_story_rag_docs,
    )

    # Source-quality gate (2026-08-25): bodyless channel posts (spam
    # channels whose messages are just "URL + Source") must NOT become
    # RAG stories — they clustered into near-identical docs that the
    # quality gate then rejected 150+/sweep. Real news has a body.
    substantive = [a for a in articles if len((a.body or "").strip()) >= _MIN_BODY_CHARS]
    skipped_bodyless = len(articles) - len(substantive)
    if skipped_bodyless:
        logger.info("tg_news:rag_skipped_bodyless count=%s", skipped_bodyless)
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
        for article in substantive
    ]
    return build_story_rag_docs(cluster_input)


async def ingest_into_rag(articles: list[NewsArticle]) -> dict[str, Any]:
    """Embed freshly-swept TG articles into the RAG ``news_v2`` collection.

    Returns ``{"stories", "ingested", "duplicates", "errors", "rejected_gate",
    "degraded", "status"}``. Fails open: a disabled gate, empty input, or a
    RAG outage all return counts without raising — but the counts say WHY.
    """
    counts: dict[str, Any] = {
        "stories": 0,
        "ingested": 0,
        "duplicates": 0,
        "errors": 0,
        "rejected_gate": 0,
        "degraded": False,
        "status": "empty",
    }
    if not articles:
        return counts
    if not _tg.TG_NEWS_RAG:
        logger.info("tg_news:rag_ingest_disabled articles=%s", len(articles))
        counts.update(disabled=True, degraded=True, status="disabled")
        return counts
    try:
        story_docs = _build_story_docs(articles)
        counts["stories"] = len(story_docs)
        # 2026-09-01 (operator, FINAL): scraped-text image extraction is
        # RETIRED. Inline image links in source posts are channel chrome
        # (the operator's X header banner shipped as "article images" three
        # times today). Post images resolve ONLY from the article's own
        # og:image via the backend thumb proxy (resolve_post_image), or the
        # badge placeholder. The image_by_url map stays (empty) so the
        # downstream story-doc shape is unchanged.
        image_by_url: dict[str, str] = {}
        counts.update(await ingest_story_docs(story_docs, image_by_url, _RAG_NEWS_COLLECTION))
    except Exception as exc:  # fail-open: a RAG outage never crashes the job
        logger.warning("tg_news:rag_ingest_failed err=%s", f"{type(exc).__name__}:{str(exc)[:180]}")
        counts["errors"] = len(articles)
    counts["status"] = _status(counts)
    counts["degraded"] = counts["status"] not in ("ok", "empty")
    logger.info(
        "tg_news:rag_ingested articles=%s stories=%s ingested=%s duplicates=%s "
        "errors=%s rejected_gate=%s status=%s degraded=%s",
        len(articles),
        counts["stories"],
        counts["ingested"],
        counts["duplicates"],
        counts["errors"],
        counts.get("rejected_gate", 0),
        counts["status"],
        counts["degraded"],
    )
    return counts


__all__ = ["ingest_into_rag"]
