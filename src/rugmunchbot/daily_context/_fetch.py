"""Daily market awareness fetchers — per-endpoint, fail-open, parallel.

Extracted verbatim from the former single-module ``daily_context.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.daily_context._constants import (
    FETCH_TIMEOUT,
    HEADLINE_MAX_CHARS,
    LAUNCH_LIMIT,
    LAUNCHES_PATH,
    MACRO_PATH,
    NEWS_LIMIT,
    NEWS_PATH,
    OVERVIEW_PATH,
    TRENDING_LIMIT,
    TRENDING_PATH,
)
from rugmunchbot.daily_context._fmt import _launch_label, _trending_label
from rugmunchbot.data_resolver import backend_get_json
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json

logger = get_logger("rmi_bot.daily_context")


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
    body = await backend_get_json(
        TRENDING_PATH, params={"limit": limit}, timeout_secs=FETCH_TIMEOUT
    )
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
        body = await http_get_json(
            f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=5
        )
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
