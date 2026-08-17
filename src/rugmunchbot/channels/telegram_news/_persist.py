"""Telegram news → website ``news_items`` persistence.

Submodule of ``channels/telegram_news.py`` — owns the **persist** half of the
pipeline: stable ``news_id`` derivation from the canonical t.me URL,
chain/token tagging (mirroring ``app.domains.news.ingest``), the
``NewsArticle`` → ``NewsItem`` row mapping (same rating card the RSS ingest
writes), and the fail-open ``news_items`` insert loop that marks articles as
ingested in the shared Redis content-hash sets only after a real insert.

``TG_NEWS_PERSIST`` is read through the shim at call time so
``patch.object(tg_news, "TG_NEWS_PERSIST", ...)`` keeps working.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import sys
from typing import TYPE_CHECKING, Any

from app.domains.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    extract_canonical_url,
    extract_image_url,
    mark_articles_ingested,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from app.domains.news_ingest.models import NewsArticle
    from app.domains.telegram.rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")


def _news_id_for(article: NewsArticle) -> str:
    """Stable news_id from the canonical t.me URL (message id = dedup key)."""
    digest = hashlib.sha256(article.source_url.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"tg:{digest}"


# Chain keyword map mirrors ``app.domains.news.ingest._detect_chains`` so TG
# articles carry the same chain tags as RSS articles on the website.
_CHAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "solana": ("solana", "sol ", "$sol"),
    "ethereum": ("ethereum", "eth ", "$eth", "ether"),
    "bitcoin": ("bitcoin", "btc ", "$btc"),
    "base": ("base ", "basechain", "coinbase base"),
    "arbitrum": ("arbitrum", "arb "),
    "polygon": ("polygon", "matic"),
    "bsc": ("bsc", "bnb chain", "binance smart chain"),
    "tron": ("tron", "trx "),
    "avalanche": ("avalanche", "avax"),
}


def _detect_chains(text: str) -> list[str]:
    """Ordered chain mentions found in article text."""
    lowered = (text or "").lower()
    return [chain for chain, keywords in _CHAIN_KEYWORDS.items() if any(k in lowered for k in keywords)]


def _tokens_from_entities(entities: list[str]) -> list[str]:
    """Map ``$TICKER`` entities to the bare ticker list the site expects."""
    return [entity[1:] for entity in entities if entity.startswith("$")]


def _article_to_news_item(article: NewsArticle) -> Any | None:
    """Map a ``NewsArticle`` to the catalog ``NewsItem`` row shape (fail-open).

    Reuses ``app.domains.news.ratings.rate_article`` (deterministic heuristic —
    the same rating card the RSS ingest writes) so TG articles get the same
    bias/sentiment/factual/tier columns on the website.
    """
    try:
        from pydantic import HttpUrl

        from app.catalog.models import NewsItem, utcnow
        from app.domains.news.ratings import rate_article

        rating = rate_article(title=article.title, content=article.body, source=article.source_channel)
        published = article.posted_at if article.posted_at is not None else utcnow()
        # ``news_id`` stays derived from the stable t.me URL (dedup key), but
        # the stored ``url`` prefers the canonical source URL so the website
        # links readers to the original article, not the channel page.
        canonical_url = extract_canonical_url(article) or article.source_url
        return NewsItem(
            news_id=_news_id_for(article),
            url=HttpUrl(canonical_url),
            title=article.title,
            summary=article.body,
            body_markdown=article.body,
            image_url=extract_image_url(article) or getattr(article, "media", None) or None,
            source=article.source_channel,
            published_at=published,
            ingested_at=utcnow(),
            chains_mentioned=_detect_chains(article.body),
            tokens_mentioned=_tokens_from_entities(article.entities),
            sentiment_score=rating.sentiment_score / 100.0,  # -1..1 column scale
            bias=rating.bias,
            sentiment_label=rating.sentiment_label,
            factual_rating=rating.factual_rating,
            source_tier=rating.source_tier,
            rating_confidence=rating.confidence,
        )
    except Exception as exc:  # fail-open: a malformed article never aborts the batch
        logger.warning("tg_news:article_build_failed title=%s err=%s", article.title[:60], str(exc)[:160])
        return None


async def _ensure_postgres_health(catalog: Any) -> bool:
    """Self-heal the cached ``catalog._health.postgres`` flag.

    The catalog's store probe (``CatalogService._init_stores``) is one-shot:
    the ``self._redis is not None`` guard makes the Postgres block run only
    once per process, so a transient failure at boot leaves the flag stuck
    ``False`` even after the container recovers. Reuse the catalog's asyncpg
    pool (creating it lazily when it never came up) and run a lightweight
    ``SELECT 1`` under a short timeout. Never cache ``False``: each failed
    persist re-probes on the next tick.
    """
    if getattr(getattr(catalog, "_health", None), "postgres", False):
        return True
    lock = getattr(catalog, "_init_lock", None)
    guard: Any = lock if lock is not None else contextlib.nullcontext()
    try:
        async with guard:
            if getattr(getattr(catalog, "_health", None), "postgres", False):
                return True  # another task recovered it while we waited
            pool = getattr(catalog, "_pg_pool", None)
            if pool is None:
                import asyncpg

                cfg = getattr(catalog, "config", None)
                pg: dict[str, Any] = (cfg or {}).get("postgres", {}) if isinstance(cfg, dict) else {}
                if not pg.get("password"):
                    return False
                catalog._pg_pool = await asyncio.wait_for(
                    asyncpg.create_pool(
                        host=pg["host"],
                        port=pg["port"],
                        user=pg["user"],
                        password=pg["password"],
                        database=pg["database"],
                        min_size=1,
                        max_size=8,
                        command_timeout=10,
                    ),
                    timeout=3,
                )
            else:

                async def _probe() -> None:
                    async with pool.acquire() as conn:
                        await conn.fetchval("SELECT 1")

                await asyncio.wait_for(_probe(), timeout=3)
            health = getattr(catalog, "_health", None)
            if health is not None:
                health.postgres = True
            logger.info("tg_news:postgres_recovered")
            return True
    except Exception as exc:
        logger.warning("tg_news:postgres_reprobe_failed err=%s", str(exc)[:160])
        return False


async def persist_articles(articles: list[NewsArticle]) -> dict[str, int]:
    """Insert new articles into ``news_items`` (website feed), deduped by PK.

    Fail-open: Postgres outage or per-item errors log and are counted; the
    caller still posts the Telegram digest. Returns
    ``{"ingested", "duplicate", "errors"}``.
    """
    counts = {"ingested": 0, "duplicate": 0, "errors": 0}
    if not _tg.TG_NEWS_PERSIST:  # type: ignore[attr-defined]
        logger.info("tg_news:persist_disabled")
        return counts
    try:
        from app.catalog.service import get_catalog

        catalog = get_catalog()
        await catalog._init_stores()  # catalog store probe (same as RSS ingest)
        if not catalog._health.postgres and not await _ensure_postgres_health(catalog):
            logger.warning("tg_news:persist_skipped_postgres_unavailable")
            return counts

        items: list[tuple[NewsArticle, Any]] = []
        for article in articles:
            item = _article_to_news_item(article)
            if item is None:
                counts["errors"] += 1
            else:
                items.append((article, item))
        if not items:
            return counts

        async with catalog._pg_pool.acquire() as conn:
            for article, item in items:
                try:
                    existing = await conn.fetchval("SELECT 1 FROM news_items WHERE news_id=$1", item.news_id)
                    if existing:
                        counts["duplicate"] += 1
                        continue
                    await conn.execute(
                        """
                        INSERT INTO news_items (
                            news_id, url, title, summary, body_markdown, image_url,
                            source, published_at, ingested_at,
                            chains_mentioned, tokens_mentioned, sentiment_score,
                            bias, sentiment_label, factual_rating, source_tier,
                            rating_confidence
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
                        """,
                        item.news_id,
                        str(item.url),
                        item.title,
                        item.summary,
                        item.body_markdown,
                        item.image_url,
                        item.source,
                        item.published_at,
                        item.ingested_at,
                        item.chains_mentioned,
                        item.tokens_mentioned,
                        item.sentiment_score,
                        item.bias,
                        item.sentiment_label,
                        item.factual_rating,
                        item.source_tier,
                        item.rating_confidence,
                    )
                    counts["ingested"] += 1
                    # Record in the shared Redis content-hash sets only AFTER a
                    # real insert, so a transient Postgres outage never hides
                    # an un-persisted story from the next sweep (fail-open).
                    mark_articles_ingested([article], DEFAULT_DEDUP_COLLECTION)
                except Exception:  # fail-open per item
                    counts["errors"] += 1
                    logger.debug("tg_news:item_insert_failed news_id=%s", item.news_id, exc_info=True)
    except Exception as exc:  # fail-open: an outage never crashes the job
        logger.warning("tg_news:persist_failed err=%s", str(exc)[:200])
        counts["errors"] = len(articles)
    return counts
