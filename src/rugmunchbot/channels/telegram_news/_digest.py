"""Telegram digest formatting + additive feed/market-awareness side-effects.

Submodule of ``channels/telegram_news.py`` — owns the **digest/broadcast
formatting** helpers (``_article_to_post_dict``, ``_category_is_news``) plus
the additive, fail-open Redis side-effects that run after every broadcast:
website full-feed injection (``inject_into_news_cache``) and the lightweight
market-awareness snapshot (``publish_market_awareness``).

``get_redis_async`` is read through the shim at call time so
``patch.object(tg_news, "get_redis_async", ...)`` keeps working.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import zlib
from collections import Counter
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.domains.news_ingest.ingest import (
    extract_canonical_url,
    extract_image_url,
    lightweight_importance,
)
from app.domains.telegram.rugmunchbot.channels.formatters import (
    NewsPost,
    format_breaking_post,
    format_feature_post,
    format_hybrid_post,
    format_news_post,
    format_themed_post,
)
from app.domains.telegram.rugmunchbot.channels.importance import top_headlines, trending_tokens
from app.domains.telegram.rugmunchbot.channels.telegram_news._persist import _news_id_for
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from app.domains.news_ingest.models import NewsArticle
    from app.domains.telegram.rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")


# ── Post to the bot's news channel ───────────────────────────────────────────


def _article_to_post_dict(article: NewsArticle) -> dict[str, Any]:
    """Shape a ``NewsArticle`` for ``format_news_post`` / ``format_breaking_post``.

    ``url`` prefers the canonical source URL (the original article link found
    inside the message, or the sweep-resolved URL when the message's only link
    was a t.me page) so digests and breaking posts link users to the real
    story instead of the channel's ``t.me`` page. ``image_url`` is populated
    when the article carries a media reference (inline image link, or the
    sweep-populated ``media`` field — og:image / photo reference).

    ``title`` prefers the sweep-populated ``post_title`` (LLM-rewritten
    headline, env-gated) and falls back to the original headline; persistence
    always keeps ``article.title`` unchanged.
    """
    canonical_url = extract_canonical_url(article) or article.resolved_url or article.source_url
    return {
        "title": article.post_title or article.title,
        "summary": article.body,
        "url": canonical_url,
        "canonical_url": canonical_url,
        "source": article.source_channel,
        "sentiment_label": "",
        "factual_rating": "",
        "image_url": extract_image_url(article) or getattr(article, "media", None) or "",
        "importance": lightweight_importance(article),
        "category": article.category or "news",
    }


def _category_is_news(category: str) -> bool:
    """True when a swept article belongs on the news channel (not alpha)."""
    return category != "alpha"


# ── Post format variety (deterministic rotation) ─────────────────────────────

#: ``$TOKEN`` matcher used for theme detection across headlines/bodies.
_TOKEN_RE = re.compile(r"\$([A-Za-z0-9]{2,10})")


def _shared_topic(items: list[dict[str, Any]]) -> str:
    """Theme label when ≥2 items mention the same ``$TOKEN`` in text, else ``""``.

    Pure + deterministic: counts distinct ``$TOKEN`` mentions per item's
    title+summary and returns the first token present in ≥2 items (most
    common first, alphabetical tiebreak). No entity extraction dependency —
    the token must actually appear in the headline/lede text.
    """
    mentions: Counter[str] = Counter()
    for item in items:
        text = f"{item.get('title') or ''} {item.get('summary') or ''}"
        seen = {m.group(1).upper() for m in _TOKEN_RE.finditer(text)}
        for tok in seen:
            mentions[tok] += 1
    for tok, count in mentions.most_common():
        if count >= 2:
            return tok
    return ""


def _batch_parity(items: list[dict[str, Any]]) -> int:
    """Deterministic 0/1 parity of a story batch (no global counter state).

    Derived from the canonical URLs so the same story set always picks the
    same format while different sets rotate digest ↔ hybrid — a stable,
    stateless "tick" signal.
    """
    seed = "|".join(str(it.get("canonical_url") or it.get("url") or "") for it in items)
    return zlib.crc32(seed.encode("utf-8")) & 1


def _news_post_for(articles: list[NewsArticle], commentaries: dict[str, str] | None = None) -> NewsPost:
    """Pick the news-channel post format deterministically for *articles*.

    Priority (breaking first, then story count / shared topic / parity):
      1. Breaking top story              → solo 🚨 post (``format_breaking_post``)
      2. Single story                    → solo ⭐ card (image + hook + lede)
      3. ≥2 stories share a ``$TOKEN``   → themed 🧵 roundup
      4. 4+ stories with even batch parity → numbered 📰 digest
      5. otherwise (≤3 stories, or 4+ with odd parity) → top-3 ⭐ hybrid

    Every format carries the RAG linkage (``image_url`` + ``canonical_url`` +
    rewritten title) through the post dicts built by ``_article_to_post_dict``.
    ``commentaries`` (optional, keyed by canonical URL / url / title — the
    same keys :func:`attach_commentaries` returns) are applied to the matching
    post dicts so each story's AI "why it matters" line renders in the post.
    """
    items = [_article_to_post_dict(a) for a in articles]
    if commentaries:
        _apply_commentaries(items, commentaries)
    if not items:
        return NewsPost("")
    if _tg.is_breaking_article(articles[0]):
        return format_breaking_post(items[0])
    if len(items) == 1:
        return format_feature_post(items[0])
    theme = _shared_topic(items)
    if theme:
        return format_themed_post(items, theme)
    if len(items) >= 4 and _batch_parity(items) == 0:
        return format_news_post(items)
    return format_hybrid_post(items)


# ── AI commentary ("why it matters" one-liner, bounded + fail-open) ─────────


#: Timeout for one snappy-tier commentary call (parallel per batch).
_COMMENTARY_TIMEOUT: float = 6.0
#: One-line length budget for the rendered "why it matters" hook.
_COMMENTARY_MAX_CHARS: int = 180

_COMMENTARY_SYSTEM: str = (
    "You are a crypto news editor. For the headline + summary you are given, "
    "write ONE short sentence (max ~25 words) explaining why this story matters "
    "to crypto traders and investors. Be concrete and specific, use only facts "
    "present in the summary, no hype, no emojis, no markdown, no leading label."
)


def _commentary_key(item: dict[str, Any]) -> str:
    """Stable key for a post-dict: canonical URL → url → title."""
    return str(item.get("canonical_url") or item.get("url") or "").strip() or str(item.get("title") or "").strip()


async def generate_commentary(item: dict[str, Any]) -> str:
    """One bounded snappy-tier "why it matters" line for a story post-dict.

    Calls ``app.llm_config.chat_with_fallback(messages, "snappy")`` under
    ``asyncio.wait_for`` (``_COMMENTARY_TIMEOUT``). Fail-open by contract:
    any error, timeout, or empty answer returns ``""`` so the post ships with
    just the headline/hook — the tick is never blocked and never crashes.
    The label ("Why it matters:") is left to the formatter; leading stars and
    any echoed label are stripped from the model output.
    """
    title = str(item.get("title") or "").strip()
    summary = str(item.get("summary") or item.get("description") or "").strip()
    if not title and not summary:
        return ""
    prompt = f"Headline: {title}\nSummary: {summary[:800]}\n\nWrite the single 'Why it matters' line now."
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _COMMENTARY_SYSTEM},
        {"role": "user", "content": prompt},
    ]
    try:
        from app.llm_config import chat_with_fallback

        text = await asyncio.wait_for(chat_with_fallback(messages, "snappy"), timeout=_COMMENTARY_TIMEOUT)
    except Exception:  # bounded + fail-open by contract
        logger.debug("tg_news:commentary_failed title=%s", title[:60], exc_info=True)
        return ""
    text = (text or "").strip().replace("\n", " ")
    low = text.lower()
    if low.startswith("why it matters:"):
        text = text[len("why it matters:") :].strip()
    if text.startswith("*") and text.endswith("*"):
        text = text.strip("*").strip()
    return text[:_COMMENTARY_MAX_CHARS].strip()


async def attach_commentaries(items: list[dict[str, Any]], *, limit: int = 5) -> dict[str, str]:
    """Generate one bounded commentary per post-dict, in parallel, fail-open.

    Returns a dict keyed by :func:`_commentary_key` → commentary text. Every
    item is attempted; a failed/empty call simply contributes nothing so the
    remaining stories still get their line. ``limit`` caps the batch (matches
    the post limit so we never call the LLM for stories that won't post).
    """
    if not items:
        return {}
    batch = items[: max(1, limit)]
    results = await asyncio.gather(*(generate_commentary(it) for it in batch), return_exceptions=True)
    out: dict[str, str] = {}
    for item, res in zip(batch, results, strict=True):
        if isinstance(res, BaseException) or not res:
            continue
        key = _commentary_key(item)
        if key:
            out[key] = res
    return out


def _apply_commentaries(items: list[dict[str, Any]], commentaries: dict[str, str]) -> None:
    """Set ``item["commentary"]`` from the keyed commentary dict (in place)."""
    for item in items:
        key = _commentary_key(item)
        if key and commentaries.get(key):
            item["commentary"] = commentaries[key]


# ── Website feed injection (additive, fail-open) ────────────────────────────


#: The Redis full-feed key the website's ``/api/v1/news/feed`` +
#: ``/api/v1/news/headlines`` endpoints serve (populated by the RSS/API
#: aggregator ``app/databus/news_provider.py``). TG articles are merged into
#: it additively so they surface on the main feed with their channel
#: attribution; the Postgres-backed ``/api/v1/news/trending`` route already
#: reads ``news_items`` directly.
_NEWS_FEED_CACHE_KEY = "rmi:news:full_feed"
_NEWS_FEED_CACHE_MAX = 50
_TG_ARTICLE_KIND = "telegram"


async def _redis_json_get(key: str) -> dict[str, Any] | None:
    """Read a JSON dict from Redis (fail-open → None)."""
    r = _tg.get_redis_async()  # type: ignore[attr-defined]
    if r is None:
        return None
    try:
        raw = await r.get(key)
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:  # fail-open by contract
        logger.debug("tg_news:redis_get_failed key=%s", key, exc_info=True)
        return None


async def _redis_json_set(key: str, data: dict[str, Any], ttl: int) -> bool:
    """Write a JSON dict to Redis. Returns True on success; fail-open → False."""
    r = _tg.get_redis_async()  # type: ignore[attr-defined]
    if r is None:
        return False
    try:
        await r.set(key, json.dumps(data, default=str), ex=ttl)
        return True
    except Exception:  # fail-open by contract
        logger.debug("tg_news:redis_set_failed key=%s", key, exc_info=True)
        return False


def _article_to_feed_item(article: NewsArticle) -> dict[str, Any]:
    """Shape a TG article for the website full-feed cache (with attribution).

    ``url`` prefers the canonical source URL (inline link, or the sweep-
    resolved URL when the message's only link was t.me) so the website links
    to the original story; ``image_url`` carries an inline image link when
    the message text includes one (the website's ``NewsArticle.image_url``
    field). Both fall back gracefully to the channel post URL / empty.
    """
    canonical_url = extract_canonical_url(article) or article.resolved_url or article.source_url
    return {
        "id": _news_id_for(article),
        "title": article.title,
        "url": canonical_url,
        "description": article.body[:300],
        "source": article.source_channel,
        "published_at": (article.posted_at or datetime.now(UTC)).isoformat(),
        "category": article.category or "news",
        "sentiment": "neutral",
        "kind": _TG_ARTICLE_KIND,
        "tier": "telegram",
        "image_url": extract_image_url(article) or getattr(article, "media", None) or "",
    }


async def inject_into_news_cache(articles: list[NewsArticle]) -> int:
    """Merge freshly-ingested TG articles into the website full-feed cache.

    The main feed (``/api/v1/news/feed`` + ``/headlines``) is served from the
    Redis cache populated by ``app.databus.news_provider`` (RSS + API only),
    so TG items would otherwise be invisible there — this prepends them with
    ``kind="telegram"`` + channel attribution. The Postgres-backed
    ``/api/v1/news/trending`` + ``POST /api/v1/news`` routes serve TG items
    directly from ``news_items`` regardless.

    Additive + fail-open: Redis down → returns 0; the aggregator's next
    periodic refresh replaces the cache wholesale and the injection re-applies
    on the next sweep. Returns the number of articles injected.
    """
    if not articles:
        return 0
    feed = await _redis_json_get(_NEWS_FEED_CACHE_KEY)
    existing: list[dict[str, Any]] = []
    if feed is not None:
        headlines = feed.get("headlines")
        if isinstance(headlines, dict):
            existing = list(headlines.get("articles") or [])
    ids = {str(a.get("id") or "") for a in existing}
    added = 0
    for article in articles:
        item = _article_to_feed_item(article)
        if item["id"] in ids:
            continue
        ids.add(item["id"])
        existing.insert(0, item)
        added += 1
        if len(existing) > _NEWS_FEED_CACHE_MAX:
            existing = existing[:_NEWS_FEED_CACHE_MAX]
    if not added:
        return 0
    merged = dict(feed or {})
    merged["headlines"] = {
        "articles": existing,
        "total": len(existing),
        "source": "rmi_news_aggregator+tg",
        "free": True,
    }
    merged["generated_at"] = datetime.now(UTC).isoformat()
    if not await _redis_json_set(_NEWS_FEED_CACHE_KEY, merged, 300):
        logger.info("tg_news:feed_cache_inject_skipped_redis_down added=%s", added)
        return 0
    logger.info("tg_news:feed_cache_injected added=%s total=%s", added, len(existing))
    return added


# ── Lightweight market-awareness snapshot (additive, fail-open) ─────────────


#: Redis key the bot's chat context can pull for a lightweight "what's moving"
#: snapshot built directly from the sweep (headlines + trending tokens).
#: ``app.domains.telegram.rugmunchbot.daily_context`` already covers the chat
#: context via ``/api/v1/news/trending`` (which includes TG items); this key
#: is the sweep's own direct artifact, refreshed every run.
_MARKET_AWARENESS_KEY = "rmi:news:market_awareness"
_MARKET_AWARENESS_TTL = 3600
_MARKET_AWARENESS_HEADLINES = 5
_MARKET_AWARENESS_TOKENS = 8


async def publish_market_awareness(articles: list[NewsArticle], scored: list[Any]) -> bool:
    """Write a lightweight market-awareness snapshot to Redis.

    Snapshot shape::

        {
            "headlines": [{"title", "source", "url"}, ...],   # top by importance
            "trending_tokens": ["SOL", "PEPE", ...],          # most-mentioned $SYMBOLs
            "article_count": int,
            "generated_at": ISO-8601,
            "source": "tg_news_sweep",
        }

    Pure selection (``importance.top_headlines`` / ``trending_tokens``) over
    the already-scored sweep; the only I/O is one fail-open Redis write.
    Returns True when the snapshot was written.
    """
    if not articles:
        return False
    try:
        headlines = [
            {
                "title": article.title,
                "source": article.source_channel,
                "url": article.source_url,
            }
            for article in top_headlines(list(scored), top_n=_MARKET_AWARENESS_HEADLINES)
        ]
        tokens = trending_tokens(articles, top_n=_MARKET_AWARENESS_TOKENS)
        snapshot = {
            "headlines": headlines,
            "trending_tokens": tokens,
            "article_count": len(articles),
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "tg_news_sweep",
        }
        if not await _redis_json_set(_MARKET_AWARENESS_KEY, snapshot, _MARKET_AWARENESS_TTL):
            logger.info("tg_news:market_awareness_skipped_redis_down articles=%s", len(articles))
            return False
        logger.info("tg_news:market_awareness_published articles=%s tokens=%s", len(articles), len(tokens))
        return True
    except Exception as exc:  # fail-open by contract
        logger.warning("tg_news:market_awareness_failed err=%s", str(exc)[:200])
        return False


# ── rugmunch.io feed publish (best-effort, fail-open) ───────────────────────


#: Master switch for publishing routed high-importance stories to the
#: rugmunch.io feed (Ghost content platform). Default ON — the publish itself
#: is best-effort and fails open when Ghost is unreachable / unauthenticated
#: (no session cookie, API key missing), so the Telegram posts are never
#: blocked by the feed publish. Canonical ``RMI_TG_NEWS_FEED_ENABLED``.
def _env_feed_bool(default: bool = True) -> bool:
    raw = os.getenv("RMI_TG_NEWS_FEED_ENABLED")
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


TG_NEWS_FEED_ENABLED: bool = _env_feed_bool()

#: Feed post type + tags for a routed news story (Ghost ``micro`` = short post).
_FEED_POST_TYPE = "micro"
_FEED_TAGS: tuple[str, ...] = ("news", "telegram")


async def publish_story_to_feed(post_text: str, *, title: str) -> dict[str, Any]:
    """Publish a routed high-importance story to the rugmunch.io feed (Ghost).

    Mirrors ``daily_market_summary.publish_to_feed``: the rugmunch.io feed is
    the Ghost content platform (``app.content_platform.create_post``). The
    publish is attempted best-effort and fails open — a missing Ghost session
    cookie / API key, network error, or Ghost HTTP error logs and returns
    ``{"error": ...}``; the Telegram posts always go out regardless. Gated by
    ``TG_NEWS_FEED_ENABLED`` (env ``RMI_TG_NEWS_FEED_ENABLED``, default on).
    """
    if not TG_NEWS_FEED_ENABLED:
        logger.info("tg_news:feed_publish_disabled title=%s", title[:80])
        return {"skipped": True}
    try:
        from app.content_platform import create_post

        return await create_post(
            content=post_text,
            post_type=_FEED_POST_TYPE,
            title=title,
            tags=list(_FEED_TAGS),
        )
    except Exception as exc:  # fail-open — feed publish is additive
        logger.warning("tg_news:feed_publish_failed title=%s err=%s", title[:80], str(exc)[:200])
        return {"error": str(exc)}
