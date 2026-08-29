"""Bounded read → normalize → time-filter → dedupe sweep + enrichment."""

from __future__ import annotations

import os  # noqa: F401
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
)

from rugmunchbot._vendor.news_ingest.discovery import discover_followed_channels  # noqa: F401
from rugmunchbot._vendor.news_ingest.normalize import is_telegram_link
from rugmunchbot.channels.telegram_news._sources._config import logger
from rugmunchbot.channels.telegram_news._sources._fetch import (  # noqa: F401
    _build_headless_client,
    _fetch_page,
    _normalize_image_url,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import ChannelMessage, NewsArticle  # noqa: F401

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from rugmunchbot._vendor.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    extract_canonical_url,
    filter_seen_articles,
    is_channel_cooldown,
    read_channel_messages,
)
from rugmunchbot._vendor.news_ingest.normalize import channel_message_to_article
from rugmunchbot.channels.telegram_news._dedup import dedupe_articles_semantic
from rugmunchbot.channels.telegram_news._sources._channels import resolve_channel_sources
from rugmunchbot.channels.telegram_news._sources._config import (
    TG_NEWS_PER_CHANNEL_LIMIT,
    TG_NEWS_SWEEP_HOURS,
)
from rugmunchbot.channels.telegram_news._sources._fetch import (
    _fetch_og_image,
    _inside_window,
    _should_fetch_og_image,
)
from rugmunchbot.channels.telegram_news._sources._tme import (
    _message_has_tme_link,
    resolve_tme_link,
)

if TYPE_CHECKING:
    from rugmunchbot._vendor.news_ingest.models import NewsArticle

import sys as _sys

_tg = _sys.modules[__package__.rsplit(".", 1)[0]]


_TITLE_REWRITE_CONCURRENCY: Final = 4


_TITLE_REWRITE_MAX_ARTICLES: Final = 12


async def _enrich_articles(articles: list[NewsArticle]) -> None:
    """Best-effort content enrichment for the posted card (fail-open).

    1. **t.me dereference** (``RMI_NEWS_TME_RESOLVE_ENABLED``, default on):
       when a message's ONLY link is a t.me page, resolve the real article
       URL onto ``article.resolved_url`` so the posted card links readers to
       the original story instead of the channel page.
    2. **LLM title rewrite** (``RMI_NEWS_TITLE_REWRITE_ENABLED``, default
       off): humanize the headline onto ``article.post_title`` (bounded to
       ``_TITLE_REWRITE_MAX_ARTICLES``). ``article.title`` keeps the
       original headline so persistence is never changed.

    Never raises and never blocks the sweep: every sub-step fails open.
    """
    if not articles:
        return

    if _tg.TME_RESOLVE_ENABLED:
        tme_only = [
            a
            for a in articles
            if is_telegram_link(a.source_url)
            and not extract_canonical_url(a)
            and _message_has_tme_link(a)
        ]
        if tme_only:
            results = await asyncio.gather(
                *(resolve_tme_link(a.source_url) for a in tme_only),
                return_exceptions=True,
            )
            for article, resolved in zip(tme_only, results, strict=True):
                if isinstance(resolved, str) and resolved and not is_telegram_link(resolved):
                    article.resolved_url = resolved

    if _tg.TITLE_REWRITE_ENABLED:
        from rugmunchbot.channels.rewrite_titles import rewrite_news_title

        sem = asyncio.Semaphore(_TITLE_REWRITE_CONCURRENCY)

        async def _rewrite_one(article: NewsArticle) -> None:
            if not (article.title or "").strip():
                return
            async with sem:
                rewritten = await rewrite_news_title(article.title)
            if rewritten and rewritten != article.title:
                article.post_title = rewritten

        await asyncio.gather(
            *(_rewrite_one(a) for a in articles[:_TITLE_REWRITE_MAX_ARTICLES]),
            return_exceptions=True,
        )


async def sweep_telegram_news(
    *,
    client: Any | None = None,
    lookback_hours: int | None = None,
    per_channel_limit: int | None = None,
) -> list[NewsArticle]:
    """Read recent messages from the configured channels.

    Returns deduped ``NewsArticle`` objects newer than ``lookback_hours``
    (default ``TG_NEWS_SWEEP_HOURS``). Accepts an injected client (any object
    exposing ``iter_messages`` or the news_ingest ``read_messages`` protocol —
    used by tests); when omitted the headless singleton is built (fail-open →
    ``[]``).

    The channel list is resolved by :func:`resolve_channel_sources`: the
    account's own followed/member channels (when ``RMI_TG_NEWS_AUTO_DISCOVER``
    is on and the client exposes ``get_dialogs``) are primary — categorized
    into news / alpha / scam / other (ALL tiers swept since 2026-08-29 —
    ``other`` ranked last; type-ranked before the MAX_CHANNELS cap)
    and bounded by ``RMI_TG_NEWS_MAX_CHANNELS`` — and the explicit
    ``RMI_TG_NEWS_CHANNELS`` / ``_DEFAULT_CHANNELS`` list is the fallback.

    Dedup is two-layered: in-batch (content hash / source URL) then
    cross-run via the shared Redis content-hash sets
    (:func:`app.domains.news_ingest.ingest.filter_seen_articles`) so content
    seen in a previous sweep — or ingested by the RAG pipeline — is never
    re-read/re-embedded (Redis down → fail-open, everything treated as fresh).
    """
    if client is None:
        client = await _tg._build_headless_client()  # type: ignore[attr-defined]
    if client is None:
        logger.warning("tg_news:sweep_skipped_no_client")
        return []

    sources = await resolve_channel_sources(client)
    if not sources:
        logger.info("tg_news:no_sources_configured")
        return []

    lookback = max(1, lookback_hours or TG_NEWS_SWEEP_HOURS)
    per_channel = max(5, per_channel_limit or TG_NEWS_PER_CHANNEL_LIMIT)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)

    articles: list[NewsArticle] = []
    for source in sources:
        username = str(source.get("username") or "").strip().lstrip("@")
        if not username:
            continue
        if is_channel_cooldown(username):
            logger.debug("tg_news:channel_cooldown_skip channel=%s", username)
            continue
        category = str(source.get("category") or "crypto_news")
        messages = await read_channel_messages(client, username, per_channel)
        for message in messages:
            if not _inside_window(message, cutoff):
                continue
            article = channel_message_to_article(message, category)
            if article is not None:
                articles.append(article)
    articles = await dedupe_articles_semantic(articles)
    articles, seen_skipped = filter_seen_articles(articles, DEFAULT_DEDUP_COLLECTION)
    if seen_skipped:
        logger.info("tg_news:cross_run_dedup skipped=%s kept=%s", seen_skipped, len(articles))
    # Best-effort story image: capture the source page's og:image for any
    # article without a real media reference (fail-open, never blocks).
    missing_images = [a for a in articles if _should_fetch_og_image(a)]
    if missing_images:
        results = await asyncio.gather(
            *(_fetch_og_image(a.source_url) for a in missing_images),
            return_exceptions=True,
        )
        for article, image in zip(missing_images, results, strict=True):
            if isinstance(image, str) and image.strip():
                article.media = image.strip()
    # Best-effort content enrichment for the posted card (env-gated,
    # fail-open): t.me-only links → real article URL; optional LLM title
    # rewrite. Never blocks the sweep.
    await _enrich_articles(articles)
    return articles
