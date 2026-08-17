#!/usr/bin/env python3
"""
RugMunchBot — LIVE daily market awareness.

Fetches a compact snapshot of *today's* crypto market from the backend and
injects it into bot responses:

  * Market overview  — BTC/ETH price, Fear & Greed, total mcap, dominance
                       (``/api/v1/market/overview`` + ``/api/v1/market/macro``)
  * Top news         — today's top stories (``/api/v1/news/trending``)
  * Trending tokens  — names + 24h change (``/api/v1/market/trending``)
  * Recent launches  — just-deployed tokens (``/api/v1/launches/recent``)

The assembled data dict is cached in Redis for 5 minutes so chat /start
paths never hammer the backend more than once per cache window. Every
fetch and cache operation is fail-open: any error degrades to an empty
section instead of breaking chat.

Stable-prefix contract:
  The digest CONTENT is volatile — it re-renders every ``CACHE_TTL`` (5 min)
  with live prices/news/trending/launches. Timestamps are DAY-GRANULARITY
  only (``%b %d %Y`` / ``%b %d`` — no HH:MM), so the *date prefix* is stable
  for the whole day, but the digest text itself is not. Consumers that build
  an LLM prompt MUST inject the digest AFTER the stable system-prompt prefix
  (e.g. appended to the user turn) — never inside the byte-stable prefix —
  otherwise the 5-minute re-render changes the shared prefix on every window.
  (Verified 2026-08-15: timestamps in both formatters are day-granularity;
  the volatile part is the data, not the date stamp.)

Public API
----------
``get_daily_context()`` -> str
    LLM-facing context block prepended to the backend chat prompt, e.g.
    "CURRENT MARKET CONTEXT (Aug 9 2026): Bitcoin $67,123, ETH $3,512,
    Fear/Greed: 62 (Greed). Top news: ... Trending: ... Recent launches: ..."
    Returns ``""`` when no data is available (fail-open).

``get_market_section()`` -> str
    Compact user-facing "Today's Market" section for the /start card.
    Returns ``""`` when no data is available.

``fetch_daily_data()`` -> dict
    Uncached parallel fetch of all sections (used by tests / reuse).

``format_daily_context(data)`` / ``format_market_section(data)``
    Pure formatters (unit-testable without network).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from datetime import UTC, datetime
from html import escape
from typing import Any

from app.core.redis import get_redis_async
from app.domains.telegram.rugmunchbot.config import fmt_number
from app.domains.telegram.rugmunchbot.data_resolver import backend_get_json
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json

logger = get_logger("rmi_bot.daily_context")

# ── Backend endpoints (all own-data; the backend caches each of these) ──
OVERVIEW_PATH = "/api/v1/market/overview"  # total mcap, fear/greed, dominance
MACRO_PATH = "/api/v1/market/macro"  # BTC/ETH spot prices + 24h change
NEWS_PATH = "/api/v1/news/trending"  # today's top stories
TRENDING_PATH = "/api/v1/market/trending"  # trending tokens
LAUNCHES_PATH = "/api/v1/launches/recent"  # just-deployed tokens

# ── Budgets ─────────────────────────────────────────────────────────────
CACHE_KEY = "rmi_bot:daily_context:v1"
CACHE_TTL = 300  # 5 minutes
FETCH_TIMEOUT = 5.0  # per-endpoint budget; all fetch in parallel
NEWS_LIMIT = 5  # fetch a few, render top 3
TRENDING_LIMIT = 10  # route default is 8; render up to 10
LAUNCH_LIMIT = 5
HEADLINE_MAX_CHARS = 110  # per-headline truncation inside the context block
CONTEXT_MAX_CHARS = 1400  # hard cap on the LLM-facing context block

# ── Small formatting helpers ────────────────────────────────────────────


def _fmt_price(value: Any) -> str:
    """Compact USD price: $67,123 / $3,512.45 / $0.0042. 'n/a' on junk."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.4f}"


def _fmt_mcap(value: Any) -> str:
    """Market cap with a trillion suffix ($3.20T) above 1T; else fmt_number."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    return fmt_number(v)


def _trending_label(item: dict[str, Any]) -> str:
    """Render a trending token as 'Name (SYM) +12.3% [chain] — address'."""
    symbol = (item.get("symbol") or "").strip().upper()
    name = (item.get("name") or "").strip()
    addr = (item.get("token_address") or item.get("address") or "").strip()
    chain = (item.get("chain") or "").strip().lower()
    change = item.get("change_24h")

    # Build label: always show name + symbol
    if name and symbol:
        label = f"{name} ({symbol})"
    elif name:
        label = name
    elif symbol:
        label = symbol
    elif addr:
        label = f"{addr[:12]}..."
    else:
        label = "Unknown Token"

    # Add change
    if change is not None:
        with contextlib.suppress(TypeError, ValueError):
            label = f"{label} {float(change):+.1f}%"

    # Add chain
    if chain:
        label = f"{label} [{chain}]"

    # Add full address (only if it's a REAL scanable on-chain address —
    # 0x hex for EVM, base58 for Solana — never a coin ID like 'thinking-cat')
    if addr and re.fullmatch(r"(0x[a-fA-F0-9]{20,40}|[1-9A-HJ-NP-Za-km-z]{32,44})", addr):
        label = f"{label} — <code>{addr}</code>"

    return label


def _launch_label(item: dict[str, Any]) -> str:
    """Render a launch item as 'Name (SYM) [chain] — address'."""
    name = (item.get("name") or "").strip()
    symbol = (item.get("symbol") or "").strip()
    chain = (item.get("chain") or "").strip().lower()
    addr = (item.get("token_address") or item.get("address") or "").strip()

    # Build label: always show name + symbol
    if name and symbol:
        label = f"{name} ({symbol})"
    elif name:
        label = name
    elif symbol:
        label = symbol
    elif addr:
        label = f"{addr[:12]}..."
    else:
        label = "Unknown Token"

    # Add chain
    if chain:
        label = f"{label} [{chain}]"

    # Add full address
    if addr:
        label = f"{label} — <code>{addr}</code>"

    return label


def _has_content(data: dict[str, Any]) -> bool:
    """True when at least one market section produced usable data."""
    prices = data.get("prices") or {}
    return bool(
        prices.get("btc") is not None
        or prices.get("eth") is not None
        or data.get("total_mcap")
        or data.get("fear_greed_value") is not None
        or data.get("news")
        or data.get("trending")
        or data.get("launches")
    )


# ── Per-endpoint fetchers (each fail-open -> empty normalized value) ────


async def _fetch_overview() -> dict[str, Any]:
    """Total mcap, BTC dominance, Fear & Greed from /market/overview.

    Since 2026-08-09 the overview endpoint also carries BTC/ETH spot
    prices + 24h change, so a single call yields both the top-line
    metrics and the flagship prices (macro stays as fallback).
    """
    body = await backend_get_json(OVERVIEW_PATH, timeout_secs=FETCH_TIMEOUT)
    if not body:
        return {}
    fg = body.get("fear_greed") or {}
    prices: dict[str, Any] = {}
    if body.get("btc_price") is not None:
        prices["btc"] = body.get("btc_price")
        prices["btc_change_24h"] = body.get("btc_change_24h")
    if body.get("eth_price") is not None:
        prices["eth"] = body.get("eth_price")
        prices["eth_change_24h"] = body.get("eth_change_24h")
    return {
        "total_mcap": body.get("total_market_cap"),
        "btc_dominance": body.get("btc_dominance"),
        "fear_greed_value": fg.get("value"),
        "fear_greed_label": fg.get("classification") or "Neutral",
        "prices": prices or None,
    }


async def _fetch_prices() -> dict[str, Any]:
    """BTC/ETH spot prices from /market/macro (assets: BTC, ETH)."""
    body = await backend_get_json(MACRO_PATH, timeout_secs=FETCH_TIMEOUT)
    prices: dict[str, Any] = {"btc": None, "eth": None}
    if not body:
        return prices
    for item in body.get("items", []):
        asset = str(item.get("asset", "")).upper()
        if asset == "BTC":
            prices["btc"] = item.get("current_price")
        elif asset == "ETH":
            prices["eth"] = item.get("current_price")
    return prices


async def _fetch_news(limit: int = NEWS_LIMIT) -> list[str]:
    """Today's top story titles from /news/trending."""
    body = await backend_get_json(NEWS_PATH, params={"limit": limit}, timeout_secs=FETCH_TIMEOUT)
    if not body:
        return []
    items = body.get("items") or body.get("articles") or []
    titles: list[str] = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            continue
        titles.append(title[:HEADLINE_MAX_CHARS])
        if len(titles) >= 3:
            break
    return titles


async def _fetch_trending(limit: int = TRENDING_LIMIT) -> list[str]:
    """Top trending token labels (name/symbol + 24h change + address)."""
    body = await backend_get_json(TRENDING_PATH, params={"limit": limit}, timeout_secs=FETCH_TIMEOUT)
    if not body:
        return []
    items = body.get("items") or []
    labels: list[str] = []
    for it in items:
        # Enrich with DexScreener if name/symbol missing
        it = await _enrich_token_name(it)
        label = _trending_label(it)
        if label and "Unknown Token" not in label and label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


async def _fetch_launches(limit: int = LAUNCH_LIMIT) -> list[str]:
    """Recent launch labels from /launches/recent (with DexScreener enrichment)."""
    body = await backend_get_json(
        LAUNCHES_PATH,
        params={"limit": limit, "enrich_bundle": False},
        timeout_secs=FETCH_TIMEOUT,
    )
    if not body:
        return []
    items = body.get("items") or []
    labels: list[str] = []
    for it in items:
        # Enrich with DexScreener if name/symbol missing
        it = await _enrich_token_name(it)
        labels.append(_launch_label(it))
        if len(labels) >= limit:
            break
    return labels


# ── DexScreener Enrichment ──────────────────────────────────────────────


async def _enrich_token_name(item: dict[str, Any]) -> dict[str, Any]:
    """Enrich a token item with name/symbol from DexScreener if missing."""
    name = (item.get("name") or "").strip()
    symbol = (item.get("symbol") or "").strip()
    if name and symbol:
        return item  # Already has both

    addr = (item.get("token_address") or item.get("address") or "").strip()
    if not addr:
        return item

    try:
        body = await http_get_json(f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=5)
        if body:
            pairs = body.get("pairs", [])
            if pairs:
                p = pairs[0]
                base = p.get("baseToken", {})
                if not name:
                    item["name"] = base.get("name", "")
                if not symbol:
                    item["symbol"] = base.get("symbol", "")
                if not item.get("chain"):
                    item["chain"] = p.get("chainId", "")
    except Exception:
        logger.debug("daily_context:dex enrichment failed for %s", addr, exc_info=True)
    return item


# ── Assembly ────────────────────────────────────────────────────────────


async def fetch_daily_data() -> dict[str, Any]:
    """Fetch all sections in parallel (uncached). Fail-open per section.

    Returns a normalized dict::

        {
            "prices": {"btc": float|None, "eth": float|None},
            "total_mcap": float|None,
            "btc_dominance": float|None,
            "fear_greed_value": int|None,
            "fear_greed_label": str,
            "news": [str, ...],      # max 3 headlines
            "trending": [str, ...],  # max 3 token labels
            "launches": [str, ...],  # max 5 launch labels
        }
    """
    results = await asyncio.gather(
        _fetch_overview(),
        _fetch_prices(),
        _fetch_news(),
        _fetch_trending(),
        _fetch_launches(),
        return_exceptions=True,
    )

    def _ok(index: int, default: Any) -> Any:
        res = results[index]
        return default if isinstance(res, BaseException) else res

    overview = _ok(0, {})
    prices = _ok(1, {"btc": None, "eth": None})
    news = _ok(2, [])
    trending = _ok(3, [])
    launches = _ok(4, [])
    # Prefer BTC/ETH prices from /market/overview (single call); macro is
    # the fallback when the overview endpoint predates the price fields.
    overview_prices = overview.get("prices") or {}
    if overview_prices.get("btc") is not None:
        prices = overview_prices
    return {
        "prices": prices,
        "total_mcap": overview.get("total_mcap"),
        "btc_dominance": overview.get("btc_dominance"),
        "fear_greed_value": overview.get("fear_greed_value"),
        "fear_greed_label": overview.get("fear_greed_label") or "Neutral",
        "news": news,
        "trending": trending,
        "launches": launches,
    }


# ── Formatters ──────────────────────────────────────────────────────────


def format_daily_context(data: dict[str, Any], when: datetime | None = None) -> str:
    """LLM-facing compact context block (the chat-injection contract).

    Format::

        CURRENT MARKET CONTEXT (Aug 9 2026): Bitcoin $67,123, ETH $3,512,
        Fear/Greed: 62 (Greed). Top news: <titles>. Trending: <tokens>.
        Recent launches: <launches>.

    Missing sections render as ``none`` / ``n/a`` so the LLM never sees a
    malformed block. Hard-capped at :data:`CONTEXT_MAX_CHARS`.
    """
    when = when or datetime.now(UTC)
    date_str = f"{when:%b} {when.day} {when.year}"
    prices = data.get("prices") or {}
    fg_value = data.get("fear_greed_value")
    fg_label = data.get("fear_greed_label") or "Neutral"
    news = data.get("news") or []
    trending = data.get("trending") or []
    launches = data.get("launches") or []

    price_bits: list[str] = []
    if prices.get("btc") is not None:
        price_bits.append(f"Bitcoin {_fmt_price(prices.get('btc'))}")
    if prices.get("eth") is not None:
        price_bits.append(f"ETH {_fmt_price(prices.get('eth'))}")
    if not price_bits:
        price_bits.append("Bitcoin n/a")
    fg_bit = f"Fear/Greed: {fg_value} ({fg_label})" if fg_value is not None else "Fear/Greed: n/a"

    context = (
        f"CURRENT MARKET CONTEXT ({date_str}): {', '.join(price_bits)}, {fg_bit}. "
        f"Top news: {'; '.join(news) if news else 'none'}. "
        f"Trending: {'; '.join(trending) if trending else 'none'}. "
        f"Recent launches: {'; '.join(launches) if launches else 'none'}."
    )
    return context[:CONTEXT_MAX_CHARS]


def format_market_section(data: dict[str, Any], when: datetime | None = None) -> str:
    """Compact user-facing "Today's Market" section for the /start card.

    HTML-escaped (the welcome card is sent with ``ParseMode.HTML``).
    """
    when = when or datetime.now(UTC)
    date_str = f"{when:%b} {when.day}"
    prices = data.get("prices") or {}
    fg_value = data.get("fear_greed_value")
    fg_label = data.get("fear_greed_label") or "Neutral"
    news = data.get("news") or []
    trending = data.get("trending") or []
    launches = data.get("launches") or []

    lines = [f"\U0001f4ca <b>Today's Market</b> ({date_str})"]
    bits: list[str] = []
    if prices.get("btc") is not None:
        bits.append(f"\U0001fa99 BTC {_fmt_price(prices.get('btc'))}")
    if prices.get("eth") is not None:
        bits.append(f"\U0001f680 ETH {_fmt_price(prices.get('eth'))}")
    if fg_value is not None:
        bits.append(f"\U0001f321\ufe0f F&G {fg_value} ({fg_label})")
    if data.get("total_mcap"):
        bits.append(f"\U0001f4c8 MCap {_fmt_mcap(data.get('total_mcap'))}")
    btc_dom = data.get("btc_dominance")
    if btc_dom is not None:
        with contextlib.suppress(TypeError, ValueError):
            bits.append(f"\U0001f4c9 BTC dom {float(btc_dom):.1f}%")
    if bits:
        lines.append(" | ".join(bits))
    if trending:
        lines.append("\U0001f525 " + " \u00b7 ".join(trending))
    if news:
        lines.append("\U0001f4f0 " + escape(news[0]))
    if launches:
        lines.append("\U0001f680 " + " \u00b7 ".join(launches))
    return "\n".join(lines)


# ── Redis cache (fail-open) ─────────────────────────────────────────────


async def _cache_get() -> dict[str, Any] | None:
    r = get_redis_async()
    if r is None:
        return None
    try:
        raw = await r.get(CACHE_KEY)
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("daily_context cache read failed", exc_info=True)
        return None


async def _cache_set(data: dict[str, Any]) -> None:
    r = get_redis_async()
    if r is None:
        return
    try:
        await r.set(CACHE_KEY, json.dumps(data), ex=CACHE_TTL)
    except Exception:
        logger.debug("daily_context cache write failed", exc_info=True)


async def _load_daily_data() -> dict[str, Any]:
    """Redis-cached (5 min) daily data; refetches only on miss/empty."""
    cached = await _cache_get()
    if cached is not None and _has_content(cached):
        return cached
    data = await fetch_daily_data()
    # Only cache non-empty snapshots so a transient outage self-heals.
    if _has_content(data):
        await _cache_set(data)
    return data


# ── Public API ──────────────────────────────────────────────────────────


async def get_daily_context() -> str:
    """LLM-facing daily market context (Redis-cached 5 min). Fail-open -> ''."""
    data = await _load_daily_data()
    if not _has_content(data):
        return ""
    return format_daily_context(data)


async def get_market_section() -> str:
    """Compact 'Today's Market' section for /start. Fail-open -> ''."""
    data = await _load_daily_data()
    if not _has_content(data):
        return ""
    return format_market_section(data)


__all__ = [
    "CACHE_KEY",
    "CACHE_TTL",
    "CONTEXT_MAX_CHARS",
    "fetch_daily_data",
    "format_daily_context",
    "format_market_section",
    "get_daily_context",
    "get_market_section",
]
