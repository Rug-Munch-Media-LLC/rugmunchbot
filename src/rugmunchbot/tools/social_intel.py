"""Social intelligence — aggregate sentiment & buzz for a token.

Sources (all ours/free): internal news-feed (bias/sentiment/factual ratings),
SearXNG meta-search for fresh social chatter. Composite output:
sentiment lean (-100..+100), mention velocity, source diversity, and a
narrative read. Real data only; gaps degrade honestly.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from rugmunchbot.config._endpoints import backend_headers
from rugmunchbot.tools.registry import ToolResult

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
SEARXNG = os.getenv("SEARXNG_URL", os.getenv("SEARXNG_BASE_URL", "http://127.0.0.1:8889"))


async def _feed_sentiment(symbol: str) -> dict[str, Any]:
    """Aggregate our rated news feed for the symbol."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(
                f"{BACKEND_URL}/api/v1/internal/news-feed",
                params={"channel": "news", "limit": 20, "q": symbol},
                headers=backend_headers(),
            )
            items = (r.json() or {}).get("items", []) if r.status_code == 200 else []
    except Exception as exc:
        logger.debug("social_intel:feed_failed err=%s", str(exc)[:100])
        return {}
    if not items:
        return {}
    sent, biased, factual = [], [], []
    for it in items:
        for k, bucket in (("sentiment", sent), ("bias_score", biased), ("factual_score", factual)):
            try:
                bucket.append(float(it.get(k)))
            except (TypeError, ValueError):
                pass
    lean = sum(sent) / len(sent) * 100 if sent else None  # -1..1 -> -100..100
    return {
        "articles": len(items),
        "sentiment_lean": round(lean, 1) if lean is not None else None,
        "avg_bias": round(sum(biased) / len(biased), 2) if biased else None,
        "avg_factual": round(sum(factual) / len(factual), 2) if factual else None,
    }


async def _buzz(symbol: str) -> dict[str, Any]:
    """SearXNG mention count across social/news surfaces (last-day freshness)."""
    try:
        async with httpx.AsyncClient(timeout=8.0) as c:
            r = await c.get(
                f"{SEARXNG}/search",
                params={"q": f"{symbol} crypto", "format": "json", "time_range": "day"},
            )
            results = (r.json() or {}).get("results", []) if r.status_code == 200 else []
    except Exception as exc:
        logger.debug("social_intel:buzz_failed err=%s", str(exc)[:100])
        return {}
    domains = {str(x.get("parsed_url", [""])[2] if x.get("parsed_url") else "") for x in results}
    return {"mentions_24h": len(results), "unique_domains": len(domains)}


async def social_intel(*, symbol: str = "", address: str = "") -> ToolResult:
    """ToolResult: composite social picture for a token."""
    sym = (symbol or "").upper().strip()
    if not sym:
        return ToolResult(tool="social_intel", ok=False,
                          data={"error": "symbol required"})
    feed, buzz = await _feed_sentiment(sym), await _buzz(sym)
    mentions = buzz.get("mentions_24h", 0)
    lean = feed.get("sentiment_lean")
    velocity = "high" if mentions >= 25 else "elevated" if mentions >= 10 else "low" if mentions else "none"
    if lean is None and not mentions:
        return ToolResult(tool="social_intel", ok=False,
                          data={"error": f"no social signals found for {sym}"})
    narrative = (
        f"{sym}: {velocity} chatter ({mentions} mentions/24h)"
        + (f", coverage leans {'bullish' if lean > 15 else 'bearish' if lean < -15 else 'mixed'} ({lean:+.0f})" if lean is not None else "")
        + (f", {feed['articles']} rated articles avg factual {feed.get('avg_factual')}" if feed.get("articles") else "")
    )
    return ToolResult(
        tool="social_intel",
        ok=True,
        data={
            "symbol": sym,
            "sentiment_lean": lean,
            "mention_velocity": velocity,
            "mentions_24h": mentions,
            "unique_domains": buzz.get("unique_domains", 0),
            "rated_articles": feed.get("articles", 0),
            "avg_bias": feed.get("avg_bias"),
            "avg_factual": feed.get("avg_factual"),
            "narrative": narrative,
        },
    )
