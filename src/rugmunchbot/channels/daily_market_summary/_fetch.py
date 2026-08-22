"""Parallel data gathering — prices, metrics, movers, narratives, KOL signals."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from rugmunchbot.channels.daily_market_summary._config import (
    logger,
)
from rugmunchbot.tools.web import NARRATIVE_QUERIES

COINGECKO_API = "https://api.coingecko.com/api/v3"


async def _fetch_major_prices() -> dict[str, Any]:
    """BTC/ETH/SOL spot + 24h change via CoinGecko ``simple/price``.

    Mirrors the existing briefing/chat market fetch (``app.domains.content
    .briefing._fetch_market_data`` / ``app.routers.market_intel``). Fail-open
    → ``{}``.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                f"{COINGECKO_API}/simple/price",
                params={
                    "ids": "bitcoin,ethereum,solana",
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "btc_price": data.get("bitcoin", {}).get("usd"),
                    "btc_change_24h": data.get("bitcoin", {}).get("usd_24h_change"),
                    "eth_price": data.get("ethereum", {}).get("usd"),
                    "eth_change_24h": data.get("ethereum", {}).get("usd_24h_change"),
                    "sol_price": data.get("solana", {}).get("usd"),
                    "sol_change_24h": data.get("solana", {}).get("usd_24h_change"),
                }
    except Exception as exc:  # fail-open — prices are optional
        logger.debug("daily_market_summary:prices_failed err=%s", str(exc)[:120])
    return {}


async def _fetch_global_metrics() -> dict[str, Any]:
    """Global market metrics via CoinGecko ``/global`` (free, no key).

    Total market cap, 24h volume and BTC dominance — the macro context the
    brief needs to describe the tape. Fail-open → ``{}``.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{COINGECKO_API}/global")
            if resp.status_code == 200:
                data = resp.json().get("data") or {}
                return {
                    "total_mcap": data.get("total_market_cap", {}).get("usd"),
                    "total_volume_24h": data.get("total_volume", {}).get("usd"),
                    "btc_dominance": data.get("market_cap_percentage", {}).get("btc"),
                    "eth_dominance": data.get("market_cap_percentage", {}).get("eth"),
                    "mcap_change_24h": data.get("market_cap_change_percentage_24h_usd"),
                }
    except Exception as exc:  # fail-open — global metrics are optional
        logger.debug("daily_market_summary:global_metrics_failed err=%s", str(exc)[:120])
    return {}


async def _fetch_market_condition() -> dict[str, Any]:
    """Market-condition verdict + fear/greed + breadth (existing tool).

    Calls ``tools/market_condition.py::market_condition`` (backend-first with
    CoinGecko/DefiLlama/alternative.me fallbacks). Returns ``{}`` on failure.
    """
    try:
        from rugmunchbot.tools.market_condition import market_condition

        result = await market_condition()
        if result is not None and result.ok and isinstance(result.data, dict):
            return result.data
    except Exception as exc:  # fail-open — sentiment is optional
        logger.debug("daily_market_summary:condition_failed err=%s", str(exc)[:120])
    return {}


async def _fetch_trending() -> list[dict[str, Any]]:
    """Trending tokens via the existing ``trending`` tool.

    Source: backend fused ``/api/v1/market/trending`` (RugCharts + DexScreener
    + CoinGecko) with a direct DexScreener token-boosts fallback. Returns [].
    """
    try:
        from rugmunchbot.tools.trending import trending

        result = await trending(chain="all", limit=8)
        if result is not None and result.ok and isinstance(result.data, dict):
            tokens = result.data.get("tokens") or []
            return [t for t in tokens if isinstance(t, dict)]
    except Exception as exc:  # fail-open — trending is optional
        logger.debug("daily_market_summary:trending_failed err=%s", str(exc)[:120])
    return []


async def _fetch_movers() -> dict[str, Any]:
    """Top gainers/losers via the backend ``/api/v1/market/movers`` endpoint.

    Real movers from CoinGecko ``/coins/markets`` + GMGN fresh pairs
    (stablecoins and flat majors filtered server-side). Returns ``{}``.
    """
    try:
        from rugmunchbot.tools.registry import backend_json_get

        body = await backend_json_get(
            "/api/v1/market/movers", params={"tf": "24h", "limit": 6}, timeout=6.0
        )
        if isinstance(body, dict):
            return {
                "gainers": [i for i in (body.get("top_gainers") or []) if isinstance(i, dict)][:6],
                "losers": [i for i in (body.get("top_losers") or []) if isinstance(i, dict)][:6],
            }
    except Exception as exc:  # fail-open — movers are optional
        logger.debug("daily_market_summary:movers_failed err=%s", str(exc)[:120])
    return {}


async def _fetch_news() -> list[dict[str, Any]]:
    """Top stories from the RAG ``news_v2`` collection via ``app.rag.tools.get_news``.

    Fails open to [] — the RAG lane must never block the summary.
    """
    try:
        from rugmunchbot._vendor.rag_pkg.tools import get_news

        payload = await get_news("top crypto market news today", top_k=6)
        results = payload.get("results") or []
        return [r for r in results if isinstance(r, dict)][:6]
    except Exception as exc:  # fail-open — news is optional
        logger.debug("daily_market_summary:news_failed err=%s", str(exc)[:120])
    return []




KOL_SIGNAL_QUERIES: tuple[str, ...] = (
    "crypto KOL shilling today",
    "crypto influencer tokens trending twitter today",
    "top crypto KOL mentions token today",
)


KOL_TOKEN_QUERIES_MAX: int = 3


_SIGNAL_SNIPPET_MAX = 180


_SIGNAL_PREVIEW_MAX = 240


_TAG_RE = re.compile(r"<[^>]+>")


_WS_RE = re.compile(r"\s+")


async def _fetch_narratives() -> list[dict[str, Any]]:
    """SearXNG hits on the day's dominant crypto narratives.

    Runs :data:`NARRATIVE_QUERIES` through ``tools.web.searxng_top_results``
    (SearXNG-only — no bridge, no knowledge fallback, ~6s bound per query,
    pooled httpx client) in parallel. Fail-open → ``[]``; each hit keeps the
    ``query`` it came from so the LLM can attribute its narrative claim.
    """
    try:
        from rugmunchbot.tools.web import searxng_top_results

        results = await asyncio.gather(
            *(searxng_top_results(q, limit=3) for q in NARRATIVE_QUERIES),
            return_exceptions=True,
        )
        out: list[dict[str, Any]] = []
        for query, res in zip(NARRATIVE_QUERIES, results, strict=True):
            if isinstance(res, BaseException) or not res:
                continue
            for r in res:
                if not isinstance(r, dict):
                    continue
                hit = dict(r)
                hit["query"] = query
                hit["snippet"] = str(hit.get("snippet") or "")[:_SIGNAL_SNIPPET_MAX]
                out.append(hit)
        return out[:12]
    except Exception as exc:  # fail-open — narratives are optional
        logger.debug("daily_market_summary:narratives_failed err=%s", str(exc)[:120])
    return []


def _kol_signal_queries(trending: list[dict[str, Any]]) -> list[str]:
    """Token-specific X-signal queries from the day's real trending tokens.

    Pure/deterministic: ``"<TICKER> crypto twitter"`` for the first
    ``KOL_TOKEN_QUERIES_MAX`` trending symbols (symbols only — never invented,
    only tokens the trending tool actually returned).
    """
    queries: list[str] = []
    for t in trending[:KOL_TOKEN_QUERIES_MAX]:
        symbol = str(t.get("symbol") or "").strip().upper()
        if symbol:
            queries.append(f"{symbol} crypto twitter trending today")
    return queries


def _html_preview(html: str, limit: int = _SIGNAL_PREVIEW_MAX) -> str:
    """Crude HTML → plain-text preview (regex-only, no parser dependency)."""
    text = _TAG_RE.sub(" ", html)
    text = _WS_RE.sub(" ", text)
    return text.strip()[:limit]


async def _kol_fetch_page(url: str, timeout: float = 4.0) -> str | None:
    """Fetch a KOL/X source page via the existing direct→FlareSolverr ladder.

    Reuses ``telegram_news._sources._fetch_page`` (direct httpx → rotating
    pool → FlareSolverr, fail-open). Returns the raw HTML (capped upstream)
    or ``None`` — the brief never blocks on a fetch; used only to verify a
    thin/empty SearXNG snippet so the LLM has real fetched content.
    """
    try:
        from rugmunchbot.channels.telegram_news._sources import _fetch_page

        return await asyncio.wait_for(_fetch_page(url, timeout), timeout=timeout + 1.0)
    except Exception as exc:  # fail-open by contract
        logger.debug(
            "daily_market_summary:kol_page_failed url=%s err=%s", url[:120], str(exc)[:120]
        )
    return None


async def _fetch_kol_signals(trending: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """SearXNG hits on what X/Twitter + KOLs are shilling right now.

    Runs :data:`KOL_SIGNAL_QUERIES` plus per-token queries built from the
    day's real trending tokens (see :func:`_kol_signal_queries`) through
    ``tools.web.searxng_top_results`` in parallel. When a hit has an empty
    snippet (search engine blocked/thin) the existing ``_fetch_page`` ladder
    (direct → FlareSolverr) verifies up to 2 pages and attaches a plain-text
    ``page_preview``. Fail-open → ``[]``.
    """
    try:
        from rugmunchbot.tools.web import searxng_top_results

        queries = list(KOL_SIGNAL_QUERIES) + _kol_signal_queries(trending)
        results = await asyncio.gather(
            *(searxng_top_results(q, limit=3) for q in queries),
            return_exceptions=True,
        )
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        pending_fetch: list[dict[str, Any]] = []
        for query, res in zip(queries, results, strict=True):
            if isinstance(res, BaseException) or not res:
                continue
            for r in res:
                if not isinstance(r, dict):
                    continue
                url = str(r.get("url") or "").strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                hit = dict(r)
                hit["query"] = query
                hit["snippet"] = str(hit.get("snippet") or "")[:_SIGNAL_SNIPPET_MAX]
                out.append(hit)
                if not hit["snippet"] and len(pending_fetch) < 2:
                    pending_fetch.append(hit)
        # Verify thin snippets through the existing fetch ladder (bounded).
        for hit in pending_fetch:
            html = await _kol_fetch_page(hit["url"])
            if html:
                preview = _html_preview(html)
                if preview:
                    hit["page_preview"] = preview
        return out[:12]
    except Exception as exc:  # fail-open — KOL signals are optional
        logger.debug("daily_market_summary:kol_signals_failed err=%s", str(exc)[:120])
    return []
