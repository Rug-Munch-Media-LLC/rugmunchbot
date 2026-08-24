# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/event_correlation.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
News-Event-to-Price Correlation Engine
=======================================
Answers: "Did this news event move the price?"

Pipeline for a token:
  1. Fetch recent news mentioning the token (Postgres ``news_items`` via
     CatalogService, with a keyword fallback over the live NewsService feed).
  2. For each news event, resolve the token's USD price just before and after
     the published timestamp using ``price_oracle.get_token_price_at_time``
     (own ClickHouse dex_events VWAP in a time window, current-price fallback).
  3. Measure the percentage price change across the event window.
  4. Classify the correlation between sentiment and price move via
     ``score_correlation``.

Public API:
    events = await correlate_news_with_price("0x...", "ethereum")
    # -> {"events": [...], "summary": "2 of 5 events correlated..."}

    label = score_correlation("BEARISH", -1.12)  # -> "NO_CORRELATION"

All external dependencies (Postgres, ClickHouse, Redis, RSS/API news sources)
are defensive: unreachable stores return empty results, never raise.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines.price_oracle import get_token_price_at_time

try:
    from asyncpg import PostgresError as AsyncPGError
except ImportError:  # pragma: no cover
    AsyncPGError = Exception

logger = get_logger(__name__)

# ── Correlation thresholds (percent) ───────────────────────────────────────
_MIN_MOVE_PCT = 0.5  # below this -> NO_CORRELATION
_MODERATE_PCT = 2.0  # aligned move above this -> MODERATE_CORRELATION
_STRONG_PCT = 5.0  # aligned move above this -> STRONG_CORRELATION

# ── Default event window (hours) ───────────────────────────────────────────
_BEFORE_HOURS = 1  # price sampled 1h before the news timestamp
_AFTER_HOURS = 4  # price sampled 4h after the news timestamp
_MAX_EVENTS = 10  # cap events returned per lookup

# ── Sentiment normalization ────────────────────────────────────────────────
_SENTIMENT_ALIASES: dict[str, str] = {
    "bullish": "BULLISH",
    "slightly_bullish": "BULLISH",
    "bearish": "BEARISH",
    "slightly_bearish": "BEARISH",
    "neutral": "NEUTRAL",
    "positive": "BULLISH",
    "negative": "BEARISH",
}


def score_correlation(sentiment: str, price_change_pct: float) -> str:
    """Classify correlation strength between news sentiment and price move.

    Args:
        sentiment: "BULLISH" | "BEARISH" | "NEUTRAL" (case-insensitive)
        price_change_pct: percent price change after the event (e.g. -1.12)

    Returns:
        "STRONG_CORRELATION" | "MODERATE_CORRELATION" | "NO_CORRELATION"
    """
    sent = _normalize_sentiment(sentiment)
    abs_change = abs(price_change_pct)
    if abs_change < _MIN_MOVE_PCT:
        return "NO_CORRELATION"

    aligned = (sent == "BULLISH" and price_change_pct > 0) or (sent == "BEARISH" and price_change_pct < 0)
    if not aligned:
        return "NO_CORRELATION"

    if abs_change > _STRONG_PCT:
        return "STRONG_CORRELATION"
    if abs_change > _MODERATE_PCT:
        return "MODERATE_CORRELATION"
    return "NO_CORRELATION"


def _normalize_sentiment(sentiment: str) -> str:
    """Normalize any sentiment label to BULLISH/BEARISH/NEUTRAL."""
    if not sentiment:
        return "NEUTRAL"
    key = str(sentiment).strip().lower()
    return _SENTIMENT_ALIASES.get(key, "NEUTRAL")


# ═══════════════════════════════════════════════════════════════════════════
# NEWS FETCHING
# ═══════════════════════════════════════════════════════════════════════════


def _token_lookup_keys(token_address: str, chain: str) -> list[str]:
    """Build candidate lookup keys for a token: ticker + address forms.

    Accepts either a contract address (0x... / base58) or a bare ticker
    ("BTC", "ETH") so BTC/ETH correlation tests work without a deployed
    contract. Returns lowercase keys safe for substring/ILIKE matching.
    """
    keys: set[str] = set()
    raw = (token_address or "").strip()
    if not raw:
        return []
    keys.add(raw.lower())

    # Bare ticker (e.g. "BTC", "ETH", "SOL")
    if re.fullmatch(r"[A-Za-z0-9]{2,12}", raw):
        keys.add(raw.lower())
        keys.add(f"${raw.lower()}")

    # Address: include short prefix + hex lower
    if raw.startswith("0x"):
        keys.add(raw.lower()[2:12])
        keys.add(raw.lower())

    # Chain natives for known pairs (e.g. WETH on ethereum)
    native_map = {
        "ethereum": ("eth", "ethereum"),
        "bsc": ("bnb", "binance coin"),
        "polygon": ("matic", "polygon"),
        "solana": ("sol", "solana"),
        "arbitrum": ("eth", "arbitrum"),
        "base": ("eth", "base"),
    }
    for key in native_map.get(_normalize_chain_name(chain), ()):
        keys.add(key.lower())
    return sorted(keys)


def _normalize_chain_name(chain: str) -> str:
    """Normalize chain aliases (eth->ethereum, bnb->bsc, ...)."""
    aliases = {
        "eth": "ethereum",
        "mainnet": "ethereum",
        "bnb": "bsc",
        "binance": "bsc",
        "matic": "polygon",
        "avax": "avalanche",
        "arb": "arbitrum",
        "op": "optimism",
        "ftm": "fantom",
    }
    return aliases.get((chain or "").strip().lower(), (chain or "").strip().lower())


def _match_news_item(item: dict[str, Any], keys: list[str]) -> bool:
    """True when a news dict mentions any token key in title/summary/tickers."""
    if not keys:
        return False
    title = str(item.get("title") or "").lower()
    summary = str(item.get("summary") or item.get("description") or "").lower()
    tickers = [str(t).lower() for t in (item.get("tokens_mentioned") or [])]
    haystack = f"{title}\n{summary}\n{' '.join(tickers)}"
    return any(key in haystack for key in keys)


async def _fetch_news_catalog(
    token_address: str,
    chain: str,
    lookback_hours: int,
    limit: int = _MAX_EVENTS,
) -> list[dict[str, Any]]:
    """Fetch recent news from the Postgres news_items table via CatalogService.

    Returns [] when Postgres is unreachable or no rows match.
    """
    try:
        from rugmunchbot._vendor.catalog_service import get_catalog

        catalog = get_catalog()
        await catalog._init_stores()
        if not catalog._health.postgres or catalog._pg_pool is None:
            return []

        cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
        keys = _token_lookup_keys(token_address, chain)
        # Build a LIKE filter over title/summary for any token key. Placeholder
        # indexes are positional (safe, parameterized) — keys never enter SQL.
        like_clauses = []
        params: list[Any] = [cutoff]
        for key in keys[:6]:  # cap for query sanity
            idx = len(params)
            like_clauses.append(
                f"(LOWER(title) LIKE ${idx + 1} OR LOWER(summary) LIKE ${idx + 2} OR "
                f"LOWER(ARRAY_TO_STRING(tokens_mentioned, ' ')) LIKE ${idx + 3})"
            )
            params.extend([f"%{key}%", f"%{key}%", f"%{key}%"])
        where = f"published_at > $1 AND ({' OR '.join(like_clauses)})" if like_clauses else "published_at > $1"
        query = (
            f"SELECT news_id, url, title, summary, source, published_at, "  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            f"sentiment_score, tokens_mentioned "
            f"FROM news_items WHERE {where} "
            f"ORDER BY published_at DESC LIMIT {int(limit)}"
        )
        async with catalog._pg_pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        items = []
        for r in rows:
            items.append(
                {
                    "news_id": r["news_id"],
                    "title": r["title"],
                    "url": r["url"],
                    "summary": r["summary"] or "",
                    "source": r["source"],
                    "published_at": r["published_at"].isoformat()
                    if hasattr(r["published_at"], "isoformat")
                    else str(r["published_at"]),
                    "sentiment_score": r["sentiment_score"],
                    "tokens_mentioned": list(r["tokens_mentioned"] or []),
                }
            )
        return items
    except (
        AsyncPGError,
        ConnectionError,
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
        ImportError,
    ) as exc:  # defensive: never break correlation on catalog issues
        logger.debug("event_correlation catalog fetch failed: %s", exc)
        return []


async def _fetch_news_live_fallback(
    token_address: str,
    chain: str,
    lookback_hours: int,
    limit: int = _MAX_EVENTS,
) -> list[dict[str, Any]]:
    """Fallback: fetch the live NewsService feed and keyword-filter locally.

    Returns [] when the feed is unreachable or no items match.
    """
    try:
        from app.domains.news.news_service.service import get_news_service

        svc = get_news_service()
        result = await svc.fetch_all(limit=150, include_rss=True, include_reddit=False)
        articles = result.get("articles", []) if isinstance(result, dict) else []
    except (
        ImportError,
        httpx.HTTPError,
        httpx.TimeoutException,
        ConnectionError,
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
    ) as exc:
        logger.debug("event_correlation live news fetch failed: %s", exc)
        return []

    keys = _token_lookup_keys(token_address, chain)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    matched: list[dict[str, Any]] = []
    for a in articles:
        if not _match_news_item(a, keys):
            continue
        pub_raw = a.get("published_at") or a.get("publishedAt") or ""
        pub_dt: datetime | None = None
        if pub_raw:
            try:
                pub_dt = datetime.fromisoformat(str(pub_raw).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pub_dt = None
        if pub_dt and pub_dt < cutoff:
            continue
        matched.append(
            {
                "news_id": a.get("id") or "",
                "title": a.get("title") or "",
                "url": a.get("url") or a.get("link") or "",
                "summary": a.get("summary") or a.get("description") or "",
                "source": a.get("source") or "News",
                "published_at": pub_dt.isoformat() if pub_dt else "",
                "sentiment_score": _sentiment_string_to_score(a.get("sentiment")),
                "tokens_mentioned": [],
            }
        )
        if len(matched) >= limit:
            break
    return matched


def _sentiment_string_to_score(sentiment: Any) -> float:
    """Convert a sentiment label to a -1..1 score (BULLISH=+0.8, BEARISH=-0.8)."""
    sent = _normalize_sentiment(str(sentiment or ""))
    if sent == "BULLISH":
        return 0.8
    if sent == "BEARISH":
        return -0.8
    return 0.0


async def fetch_token_news(
    token_address: str,
    chain: str,
    lookback_hours: int = 24,
    limit: int = _MAX_EVENTS,
) -> list[dict[str, Any]]:
    """Fetch recent news events mentioning a token.

    Primary source: Postgres news_items (CatalogService). Falls back to the
    live NewsService feed when the catalog is unreachable or returns nothing.
    """
    items = await _fetch_news_catalog(token_address, chain, lookback_hours, limit=limit)
    if not items:
        items = await _fetch_news_live_fallback(token_address, chain, lookback_hours, limit=limit)
    return items[:limit]


# ═══════════════════════════════════════════════════════════════════════════
# PRICE SAMPLING
# ═══════════════════════════════════════════════════════════════════════════


def _parse_published_at(raw: str | None) -> datetime | None:
    """Parse an ISO-8601 published_at string into a timezone-aware datetime."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except (ValueError, TypeError):
        return None


def _sentiment_from_score(score: Any) -> str:
    """Map a numeric sentiment_score (-1..1) to BULLISH/BEARISH/NEUTRAL."""
    try:
        s = float(score or 0)
    except (ValueError, TypeError):
        return "NEUTRAL"
    if s > 0.15:
        return "BULLISH"
    if s < -0.15:
        return "BEARISH"
    return "NEUTRAL"


def _price_change_pct(price_before: float, price_after: float) -> float:
    """Percent change from before to after (0.0 when before is 0)."""
    if not price_before or price_before <= 0:
        return 0.0
    return round(((price_after - price_before) / price_before) * 100, 2)


async def _sample_price_at(
    token_address: str,
    chain: str,
    at_time: datetime,
) -> dict[str, Any] | None:
    """Resolve token price at a timestamp (defensive wrapper)."""
    try:
        return await get_token_price_at_time(token_address, chain, at_time)
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        ImportError,
        httpx.HTTPError,
        httpx.TimeoutException,
    ) as exc:  # defensive: never break the correlation loop
        logger.debug("event_correlation price sample failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def correlate_news_with_price(
    token_address: str,
    chain: str,
    lookback_hours: int = 24,
) -> dict[str, Any]:
    """Correlate recent news events with token price movements.

    Args:
        token_address: Token contract address OR bare ticker (BTC/ETH/SOL)
        chain: Chain name (ethereum, bsc, solana, ...)
        lookback_hours: How far back to search for news events

    Returns:
        {
            "events": [
                {
                    "title": str,
                    "url": str,
                    "published_at": ISO str,
                    "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL",
                    "price_before": float,
                    "price_after": float,
                    "price_change_pct": float,
                    "correlation": "STRONG_CORRELATION" | "MODERATE_CORRELATION" | "NO_CORRELATION",
                    "price_source": str,
                    "event_ts": float (epoch),
                }
            ],
            "summary": "2 of 5 events correlated with price movements",
            "lookback_hours": int,
            "token_address": str,
            "chain": str,
        }
    """
    news = await fetch_token_news(token_address, chain, lookback_hours)
    events: list[dict[str, Any]] = []

    for item in news:
        pub_dt = _parse_published_at(item.get("published_at"))
        if pub_dt is None:
            continue

        before_dt = pub_dt - timedelta(hours=_BEFORE_HOURS)
        after_dt = pub_dt + timedelta(hours=_AFTER_HOURS)

        before = await _sample_price_at(token_address, chain, before_dt)
        after = await _sample_price_at(token_address, chain, after_dt)
        if before is None or after is None:
            continue
        price_before = float(before.get("price_usd", 0) or 0)
        price_after = float(after.get("price_usd", 0) or 0)
        if price_before <= 0 or price_after <= 0:
            continue

        change_pct = _price_change_pct(price_before, price_after)
        sentiment = _sentiment_from_score(item.get("sentiment_score"))
        correlation = score_correlation(sentiment, change_pct)

        events.append(
            {
                "title": str(item.get("title") or "Untitled")[:200],
                "url": str(item.get("url") or ""),
                "published_at": pub_dt.isoformat(),
                "sentiment": sentiment,
                "price_before": round(price_before, 10),
                "price_after": round(price_after, 10),
                "price_change_pct": change_pct,
                "correlation": correlation,
                "price_source": str(before.get("source") or ""),
                "event_ts": pub_dt.timestamp(),
            }
        )

    correlated = [e for e in events if e["correlation"] != "NO_CORRELATION"]
    if events:
        summary = f"{len(correlated)} of {len(events)} events correlated with price movements"
    else:
        summary = "No news events found for this token in the lookback window"

    return {
        "events": events,
        "summary": summary,
        "lookback_hours": int(lookback_hours),
        "token_address": str(token_address),
        "chain": str(chain),
    }
