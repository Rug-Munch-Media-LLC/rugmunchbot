"""Midday Market Report — BROADER-markets + macro gathering.

DISTINCT source facet from the Morning Brief. The Morning Brief covers
crypto stories; this report covers the MACRO tape and its effect on crypto:

* Macro intel — rugbrain ``market_health`` + ``regulation_enforcement`` +
  ``news_v2`` via the bot-side direct-Qdrant lane, queried for Fed / rates /
  CPI / jobless / DXY / equities.
* Crypto tape — CoinGecko global metrics + BTC/ETH/SOL spot (the effect
  side: how the macro tape is landing on crypto).
* Ranked stories — the reranker, filtered to MACRO-relevant items only
  (a crypto-only story belongs to the Morning Brief, not here).

Every lane is fail-open; a broken lane contributes nothing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger("rmi_bot.channels.midday_market_report")

#: RAG collections for the macro facet (NOT the crypto-story facet).
_RAG_COLLECTIONS = ["market_health", "regulation_enforcement", "news_v2"]
_RAG_QUERY = (
    "macro markets today: Federal Reserve interest rates FOMC, CPI inflation, "
    "jobless claims, treasury yields, US dollar DXY, S&P 500 Nasdaq equities, "
    "gold, oil — and how they are affecting bitcoin and crypto "
    "last 24 hours only, no stories older than today"
)
_RAG_LIMIT = 5  # per collection — dedup keeps the prompt small

#: The report covers the last 24h.
_WINDOW_HOURS = 24.0

#: Macro keywords — a ranked story must match one to belong to this report.
_MACRO_KEYWORDS = (
    "fed",
    "federal reserve",
    "fomc",
    "rate cut",
    "rate hike",
    "interest rate",
    "cpi",
    "inflation",
    "jobless",
    "unemployment",
    "payroll",
    "gdp",
    "recession",
    "treasury",
    "yield",
    "dxy",
    "dollar index",
    "s&p",
    "nasdaq",
    "dow",
    "equities",
    "stocks",
    "gold",
    "oil",
    "macro",
    "tariff",
    "sec",
    "etf",
)


def _is_macro_story(story: dict[str, Any]) -> bool:
    """True when a ranked story is macro-relevant (belongs to this report)."""
    blob = " ".join(str(story.get(k) or "") for k in ("title", "summary", "category")).lower()
    return any(kw in blob for kw in _MACRO_KEYWORDS)


async def _fetch_macro_intel() -> list[dict[str, Any]]:
    """Query rugbrain macro collections (fail-open)."""
    from rugmunchbot.tools.rag_lookup import _rag_direct_qdrant, _rag_direct_search_multi

    hits = await _rag_direct_qdrant(_RAG_QUERY, _RAG_COLLECTIONS, _RAG_LIMIT)
    if not hits:
        hits = await _rag_direct_search_multi(_RAG_QUERY, _RAG_COLLECTIONS, _RAG_LIMIT * 2)
    if not hits:
        return []
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for h in sorted(hits, key=lambda x: float(x.get("score") or 0.0), reverse=True):
        content = str(h.get("content") or "").strip()
        if not content:
            continue
        key = " ".join(content.lower().split())[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
        if len(out) >= 10:
            break
    return out


async def _fetch_macro_stories() -> list[dict[str, Any]]:
    """Reranker stories filtered to the MACRO facet (fail-open)."""
    from rugmunchbot.channels._news_rerank import fetch_top_ranked_stories

    try:
        stories = await fetch_top_ranked_stories(window_hours=_WINDOW_HOURS)
    except Exception as exc:  # fail-open — never block the report
        logger.warning("midday_report:ranked_stories_failed err=%s", str(exc)[:140])
        return []

    cutoff = datetime.now(UTC) - timedelta(hours=_WINDOW_HOURS)
    fresh: list[dict[str, Any]] = []
    for item in stories:
        if not _is_macro_story(item):
            continue
        raw = item.get("published_at")
        dt: datetime | None = None
        try:
            if isinstance(raw, (int, float)):
                ts = float(raw)
                if ts > 1e12:
                    ts /= 1000.0
                dt = datetime.fromtimestamp(ts, tz=UTC)
            elif raw:
                dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
        except (ValueError, TypeError, OSError):
            dt = None
        if dt is not None and dt >= cutoff:
            fresh.append(item)
    return fresh


async def _fetch_crypto_tape() -> dict[str, Any]:
    """Crypto tape (global metrics + majors) — the effect side (fail-open)."""
    from rugmunchbot.channels.daily_market_summary._fetch import (
        _fetch_global_metrics,
        _fetch_major_prices,
    )

    prices, glob = await asyncio.gather(
        _fetch_major_prices(), _fetch_global_metrics(), return_exceptions=True
    )
    return {
        "prices": prices if not isinstance(prices, BaseException) else {},
        "global_metrics": glob if not isinstance(glob, BaseException) else {},
    }


async def gather_midday_report_data() -> dict[str, Any]:
    """Pull every report block in parallel; never raises.

    Returns ``{macro_intel, macro_stories, prices, global_metrics, sources,
    generated_at}``. Every block degrades to an empty value.
    """
    results = await asyncio.gather(
        _fetch_macro_intel(),
        _fetch_macro_stories(),
        _fetch_crypto_tape(),
        return_exceptions=True,
    )
    macro_intel, macro_stories, tape = (
        r if not isinstance(r, BaseException) else ([] if i < 2 else {})
        for i, r in enumerate(results)
    )
    for name, value in (
        ("macro_intel", macro_intel),
        ("macro_stories", macro_stories),
        ("tape", tape),
    ):
        if isinstance(value, BaseException):
            logger.warning("midday_report:lane_failed lane=%s err=%s", name, str(value)[:120])

    tape = tape if isinstance(tape, dict) else {}
    prices = tape.get("prices") or {}
    glob = tape.get("global_metrics") or {}

    sources = [
        name
        for name, value in (
            ("macro_intel", macro_intel),
            ("macro_stories", macro_stories),
            ("prices", prices),
            ("global_metrics", glob),
        )
        if value
    ]
    return {
        "macro_intel": macro_intel or [],
        "macro_stories": macro_stories or [],
        "prices": prices,
        "global_metrics": glob,
        "sources": sources,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _serialize_context(context: dict[str, Any]) -> str:
    """Compact, LLM-friendly text of the gathered macro data (cite-or-omit)."""
    parts: list[str] = []

    stories = context.get("macro_stories") or []
    if stories:
        lines = ["MACRO STORIES (reranker-ordered, best first):"]
        for i, s in enumerate(stories[:8], 1):
            title = str(s.get("title") or "").strip()
            summary = str(s.get("summary") or "").strip()[:220]
            src = str(s.get("source") or "").strip()
            url = str(s.get("url") or "").strip()
            bits = [f"{i}. {title or summary[:80]}"]
            if src:
                bits.append(f"source {src}")
            if summary and title:
                bits.append(summary)
            line = " — ".join(bits)
            if url:
                line += f" [url={url}]"
            lines.append(line)
        parts.append("\n".join(lines))

    intel = context.get("macro_intel") or []
    if intel:
        lines = ["MACRO INTEL (market_health / regulation_enforcement / news_v2):"]
        for h in intel[:8]:
            content = str(h.get("content") or "").strip()[:300]
            meta = h.get("metadata") or {}
            url = str(meta.get("url") or h.get("url") or "").strip()
            coll = str(h.get("collection") or "").strip()
            line = f"- {content}"
            if coll:
                line += f" [collection={coll}]"
            if url:
                line += f" [url={url}]"
            lines.append(line)
        parts.append("\n".join(lines))

    prices = context.get("prices") or {}
    if prices:
        lines = ["CRYPTO TAPE (CoinGecko spot):"]
        for sym, row in list(prices.items())[:6]:
            if not isinstance(row, dict):
                continue
            usd = row.get("usd")
            chg = row.get("usd_24h_change")
            if usd is None:
                continue
            line = f"- {sym}: ${usd:,.2f}"
            if chg is not None:
                line += f" ({chg:+.2f}% 24h)"
            lines.append(line)
        parts.append("\n".join(lines))

    glob = context.get("global_metrics") or {}
    if glob:
        lines = ["GLOBAL CRYPTO METRICS:"]
        if glob.get("total_mcap") is not None:
            lines.append(f"- total mcap ${glob['total_mcap']:,.0f}")
        if glob.get("total_volume_24h") is not None:
            lines.append(f"- 24h volume ${glob['total_volume_24h']:,.0f}")
        if glob.get("btc_dominance") is not None:
            lines.append(f"- BTC dominance {glob['btc_dominance']:.1f}%")
        if len(lines) > 1:
            parts.append("\n".join(lines))

    return "\n\n".join(parts) if parts else "(no data)"


def pick_midday_image(context: dict[str, Any]) -> str:
    """Best available image for the report (story og:image, else "")."""
    for story in context.get("macro_stories") or []:
        image = str(story.get("image_url") or story.get("image") or "").strip()
        if image.startswith(("http://", "https://")):
            return image
    for hit in context.get("macro_intel") or []:
        meta = hit.get("metadata") or {}
        image = str(meta.get("image_url") or hit.get("image_url") or "").strip()
        if image.startswith(("http://", "https://")):
            return image
    return ""


__all__ = [
    "_serialize_context",
    "gather_midday_report_data",
    "pick_midday_image",
]
