"""Daily Market Brief — 7:00 AM ET editorial market intelligence.

A scheduled job (registered from ``channels/scheduler.py`` as
``daily_market_summary``) that runs every morning at **07:00 America/New_York**
and writes + posts a high-quality editorial market brief to the operator's
main channel (@cryptorugmuncher) and the rugmunch.io feed (Ghost content
platform).

Pipeline (per tick, fail-open at every step — a broken source or LLM skips
the post, it never crashes the JobQueue):

    1. **Gather** — :func:`gather_summary_data` pulls, in parallel and
       best-effort, from the existing data sources (no new services):
       BTC/ETH/SOL prices + 24h change (CoinGecko ``simple/price``), market
       condition verdict + fear/greed + breadth
       (``tools/market_condition.py::market_condition``), trending tokens
       (``tools/trending.py::trending`` — backend fused feed with DexScreener
       fallback), top gainers/losers (backend ``/api/v1/market/movers``),
       RAG ``news_v2`` top stories (``app.rag.tools.get_news``), plus two
       web-signal blocks from SearXNG (``tools/web.py::searxng_top_results``):
       the day's dominant **narratives** (``_fetch_narratives``) and
       **X/Twitter + KOL shilling signals** (``_fetch_kol_signals``, driven
       off the day's real trending tokens, with page verification through the
       existing ``telegram_news._sources._fetch_page`` direct→FlareSolverr
       ladder when a snippet is thin). Every source is optional; each failure
       logs and contributes nothing.
    2. **Write** — :func:`post_daily_market_summary` builds the prompt and
       calls ``app.llm_config.chat_with_fallback(messages, "long")``
       (Nemotron 3 Ultra via OpenRouter, paid ``:lowest`` fallback) which
       returns a Telegram-HTML editorial: market overview → 🧭 narrative →
       trending movers (with % + why) → top news → sentiment → 📣 X / KOL
       signals → "what to watch today". The KOL lines are real, sourced
       mentions only (source links included) — the writer never invents a
       shill. When the LLM returns empty the job logs and skips — never
       fabricates.
    3. **Clean** — :func:`cleanup_html` normalizes markdown-ish artifacts to
       safe Telegram HTML and enforces the char budget (≤ 3500).
    4. **Post** — the report goes to ``_chat_id("main")`` (the registry
       ``main`` role → @cryptorugmuncher) via the scheduler's retrying
       ``_safe_send`` (ParseMode.HTML). When posting succeeds the same content
       is published to the rugmunch.io feed via
       ``app.content_platform.create_post`` (post_type="briefing", tags
       ``["daily", "market-summary"]``) — fail-open, the Telegram post is never
       blocked by the feed publish.

Env knobs (all optional). New ``RMI_DAILY_MARKET_BRIEF_*`` names are
canonical; the legacy ``RMI_DAILY_MARKET_SUMMARY_*`` names still work as
aliases (the new name wins when both are set):
* ``RMI_DAILY_MARKET_BRIEF_ENABLED`` — master switch (default ON)
* ``RMI_DAILY_MARKET_BRIEF_TIME``   — HH:MM in ``RMI_DAILY_MARKET_BRIEF_TZ``
  (default ``"07:00"`` — 7:00 AM ET)
* ``RMI_DAILY_MARKET_BRIEF_TZ``     — IANA timezone (default
  ``"America/New_York"``)
* ``RMI_DAILY_MARKET_BRIEF_MAX_CHARS`` — report length budget
  (default 3500, hard-capped under Telegram's 4096 soft limit)
* ``RMI_DAILY_MARKET_BRIEF_FEED_ENABLED`` — publish to the rugmunch.io feed
  (default ON; no-op without a Ghost session cookie — ``create_post`` fails
  open)
* ``RMI_DAILY_MARKET_BRIEF_WEB_SIGNALS_ENABLED`` — gate for the two
  web-search sections (🧭 NARRATIVE + 📣 X / KOL SIGNALS; default ON — when
  OFF the brief runs on the deterministic market data only, the old behavior)
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.channels.daily_market_summary")

# ── Env knobs ───────────────────────────────────────────────────────────────


def _env_bool_alias(new: str, old: str, default: bool) -> bool:
    """Read the canonical (renamed) env var, falling back to the legacy name.

    ``RMI_DAILY_MARKET_BRIEF_*`` is canonical; ``RMI_DAILY_MARKET_SUMMARY_*``
    keeps working as an alias so existing deployments don't need edits. The
    new name wins when both are set.
    """
    raw = os.getenv(new)
    if raw is None:
        raw = os.getenv(old)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int_alias(new: str, old: str, default: int) -> int:
    raw = os.getenv(new)
    if raw is None:
        raw = os.getenv(old)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_str_alias(new: str, old: str, default: str) -> str:
    raw = os.getenv(new)
    if raw is None:
        raw = os.getenv(old)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


#: Master switch. Unlike the personal channel this defaults ON — the daily
#: market brief is a core scheduled surface (7 days/week) — but operators can
#: disable it with ``RMI_DAILY_MARKET_BRIEF_ENABLED=0`` (legacy
#: ``RMI_DAILY_MARKET_SUMMARY_ENABLED`` still works as an alias).
DAILY_MARKET_SUMMARY_ENABLED: bool = _env_bool_alias(
    "RMI_DAILY_MARKET_BRIEF_ENABLED", "RMI_DAILY_MARKET_SUMMARY_ENABLED", True
)

#: Report length budget (default 3500 chars per the spec; Telegram soft limit
#: is 4096, so 3500 leaves headroom for the send layer).
DAILY_MARKET_SUMMARY_MAX_CHARS: int = _env_int_alias(
    "RMI_DAILY_MARKET_BRIEF_MAX_CHARS", "RMI_DAILY_MARKET_SUMMARY_MAX_CHARS", 3500
)

#: Publish the same content to the rugmunch.io feed (Ghost content platform)
#: after a successful Telegram post. Fail-open with no Ghost cookie.
DAILY_MARKET_SUMMARY_FEED_ENABLED: bool = _env_bool_alias(
    "RMI_DAILY_MARKET_BRIEF_FEED_ENABLED", "RMI_DAILY_MARKET_SUMMARY_FEED_ENABLED", True
)

#: Gate for the two web-search sections (🧭 NARRATIVE + 📣 X / KOL SIGNALS).
#: Default ON; when OFF the brief runs on the deterministic market data only
#: (the pre-brief behavior). Canonical ``RMI_DAILY_MARKET_BRIEF_WEB_SIGNALS_ENABLED``
#: with the legacy ``RMI_DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED`` alias.
DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED: bool = _env_bool_alias(
    "RMI_DAILY_MARKET_BRIEF_WEB_SIGNALS_ENABLED",
    "RMI_DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED",
    True,
)

#: IANA timezone for the 7:00 AM daily fire (default America/New_York = ET).
DAILY_MARKET_SUMMARY_TZ: str = _env_str_alias(
    "RMI_DAILY_MARKET_BRIEF_TZ", "RMI_DAILY_MARKET_SUMMARY_TZ", "America/New_York"
)

#: ``HH:MM`` of day in ``DAILY_MARKET_SUMMARY_TZ`` (default 07:00 ET).
DAILY_MARKET_SUMMARY_TIME: str = _env_str_alias("RMI_DAILY_MARKET_BRIEF_TIME", "RMI_DAILY_MARKET_SUMMARY_TIME", "07:00")


def summary_run_time() -> Any:
    """``datetime.time`` (tz-aware) for the APScheduler daily trigger.

    Parses ``RMI_DAILY_MARKET_SUMMARY_TIME`` in
    ``RMI_DAILY_MARKET_SUMMARY_TZ``; any parse failure falls back to 07:00 in
    the configured (default ET) zone — the job must always schedule.
    """
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    try:
        hour, minute = (int(p) for p in DAILY_MARKET_SUMMARY_TIME.split(":", 1))
        hour, minute = max(0, min(23, hour)), max(0, min(59, minute))
    except (ValueError, TypeError):
        logger.warning(
            "daily_market_summary:bad_time value=%s — using 07:00",
            DAILY_MARKET_SUMMARY_TIME,
        )
        hour, minute = 7, 0
    try:
        tz = ZoneInfo(DAILY_MARKET_SUMMARY_TZ)
    except Exception as exc:  # zoneinfo raises ZoneInfoNotFoundError for bad tz
        logger.warning("daily_market_summary:bad_tz value=%s err=%s", DAILY_MARKET_SUMMARY_TZ, exc)
        tz = ZoneInfo("America/New_York")
    return dtime(hour=hour, minute=minute, tzinfo=tz)


# ── Pure helpers ────────────────────────────────────────────────────────────


def _fmt_price(value: Any) -> str:
    """USD price or 'N/A' — never invents a number."""
    if value is None or value == "" or value == 0:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.4f}"


def _fmt_pct(value: Any) -> str:
    """Signed 24h % with arrows; missing → 'N/A'."""
    if value is None or value == "":
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v > 0:
        return f"+{v:.1f}%"
    if v < 0:
        return f"{v:.1f}%"
    return "0.0%"


def _fmt_volume(value: Any) -> str:
    """Compact USD volume (K/M/B) or 'N/A'."""
    if value is None or value == "" or value == 0:
        return "N/A"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if abs(v) >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.0f}"


# ── Gather (async, best-effort, never raises) ──────────────────────────────


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


async def _fetch_market_condition() -> dict[str, Any]:
    """Market-condition verdict + fear/greed + breadth (existing tool).

    Calls ``tools/market_condition.py::market_condition`` (backend-first with
    CoinGecko/DefiLlama/alternative.me fallbacks). Returns ``{}`` on failure.
    """
    try:
        from app.domains.telegram.rugmunchbot.tools.market_condition import market_condition

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
        from app.domains.telegram.rugmunchbot.tools.trending import trending

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
        from app.domains.telegram.rugmunchbot.tools.registry import backend_json_get

        body = await backend_json_get("/api/v1/market/movers", params={"tf": "24h", "limit": 6}, timeout=6.0)
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
        from app.rag.tools import get_news

        payload = await get_news("top crypto market news today", top_k=6)
        results = payload.get("results") or []
        return [r for r in results if isinstance(r, dict)][:6]
    except Exception as exc:  # fail-open — news is optional
        logger.debug("daily_market_summary:news_failed err=%s", str(exc)[:120])
    return []


# ── Web signals (SearXNG + the existing fetch ladder) — fail-open ─────────


#: SearXNG queries used to surface the day's dominant crypto narrative(s).
#: Thematic sector queries (not ticker-specific) — the LLM synthesizes the
#: 1-2 dominant narratives from these hits, cite-or-omit.
NARRATIVE_QUERIES: tuple[str, ...] = (
    "crypto dominant narrative today sector",
    "AI agents crypto narrative news today",
    "RWA tokenization crypto news today",
    "crypto restaking layer 2 narrative news today",
)

#: Base SearXNG queries for X/Twitter + KOL shilling signals. Token-specific
#: queries ("<TICKER> crypto twitter") are added at runtime from the day's
#: real trending tokens (see :func:`_kol_signal_queries`).
KOL_SIGNAL_QUERIES: tuple[str, ...] = (
    "crypto KOL shilling today",
    "crypto influencer tokens trending twitter today",
    "top crypto KOL mentions token today",
)

#: How many trending tokens get a per-token X-signal query (keeps the search
#: count bounded — SearXNG is self-hosted/free, but every query adds ~6s).
KOL_TOKEN_QUERIES_MAX: int = 3

#: Cap for the per-hit snippet/preview handed to the LLM (token budget).
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
        from app.domains.telegram.rugmunchbot.tools.web import searxng_top_results

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
        from app.domains.telegram.rugmunchbot.channels.telegram_news._sources import _fetch_page

        return await asyncio.wait_for(_fetch_page(url, timeout), timeout=timeout + 1.0)
    except Exception as exc:  # fail-open by contract
        logger.debug("daily_market_summary:kol_page_failed url=%s err=%s", url[:120], str(exc)[:120])
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
        from app.domains.telegram.rugmunchbot.tools.web import searxng_top_results

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


async def gather_summary_data() -> dict[str, Any]:
    """Pull every brief block from the existing data sources, in parallel.

    Returns a dict with keys ``prices`` (raw CoinGecko dict), ``condition``
    (market-condition payload), ``trending`` (token list), ``movers``
    (``{gainers, losers}``), ``news`` (RAG hits), ``narratives`` (SearXNG
    narrative hits), ``kol_signals`` (SearXNG X/KOL hits) plus ``sources``
    (which fetchers succeeded) and ``generated_at``. The two web-signal
    blocks only run when ``DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED`` and
    never gate the brief — a broken SearXNG contributes nothing. This
    function never raises — every block degrades to an empty value.
    """
    prices, condition, trending, movers, news = await asyncio.gather(
        _fetch_major_prices(),
        _fetch_market_condition(),
        _fetch_trending(),
        _fetch_movers(),
        _fetch_news(),
        return_exceptions=True,
    )
    blocks = [prices, condition, trending, movers, news]
    cleaned = [b if not isinstance(b, BaseException) else {} for b in blocks]
    sources = ["prices", "condition", "trending", "movers", "news"]
    active = [s for s, v in zip(sources, cleaned, strict=True) if v]

    narratives: list[Any] = []
    kol_signals: list[Any] = []
    if DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED:
        trends = cleaned[2] if isinstance(cleaned[2], list) else []
        narratives, kol_signals = await asyncio.gather(
            _fetch_narratives(),
            _fetch_kol_signals(trends),
            return_exceptions=True,
        )
        narratives = [] if isinstance(narratives, BaseException) else narratives
        kol_signals = [] if isinstance(kol_signals, BaseException) else kol_signals
        if narratives:
            active.append("narratives")
        if kol_signals:
            active.append("kol_signals")

    return {
        "prices": cleaned[0] if isinstance(cleaned[0], dict) else {},
        "condition": cleaned[1] if isinstance(cleaned[1], dict) else {},
        "trending": cleaned[2] if isinstance(cleaned[2], list) else [],
        "movers": cleaned[3] if isinstance(cleaned[3], dict) else {},
        "news": cleaned[4] if isinstance(cleaned[4], list) else [],
        "narratives": narratives,
        "kol_signals": kol_signals,
        "sources": active,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


# ── Prompt + serialization (pure) ──────────────────────────────────────────


def _serialize_context(context: dict[str, Any]) -> str:
    """Compact, LLM-friendly text of the gathered data (never the raw dump).

    Renders only the fields the writer needs, with the exact numbers the
    sources returned — the LLM is instructed to cite-or-omit, so the char
    budget stays tight and the report stays grounded.
    """
    parts: list[str] = []
    prices = context.get("prices") or {}
    if prices:
        parts.append(
            "MAJOR PRICES (24h): "
            f"BTC {_fmt_price(prices.get('btc_price'))} ({_fmt_pct(prices.get('btc_change_24h'))}) · "
            f"ETH {_fmt_price(prices.get('eth_price'))} ({_fmt_pct(prices.get('eth_change_24h'))}) · "
            f"SOL {_fmt_price(prices.get('sol_price'))} ({_fmt_pct(prices.get('sol_change_24h'))})"
        )

    condition = context.get("condition") or {}
    if condition:
        signals = condition.get("signals") or {}
        fg = signals.get("fear_greed") or {}
        if isinstance(fg, dict) and fg.get("value") is not None:
            parts.append(f"FEAR & GREED: {fg.get('value')}/100 ({fg.get('classification') or 'N/A'})")
        verdict = condition.get("verdict")
        if verdict:
            parts.append(f"MARKET VERDICT: {verdict} (confidence {condition.get('confidence') or 'low'})")
        reasons = condition.get("reasons") or []
        if reasons:
            parts.append("VERDICT REASONS: " + " | ".join(str(r) for r in reasons[:4]))
        mcap = signals.get("global_market_cap_usd")
        mcap_chg = signals.get("global_market_cap_change_24h")
        if mcap is not None or mcap_chg is not None:
            parts.append(f"GLOBAL MCAP: {_fmt_volume(mcap)} ({_fmt_pct(mcap_chg)})")

    movers = context.get("movers") or {}
    gainers = movers.get("gainers") or []
    losers = movers.get("losers") or []
    if gainers or losers:
        lines: list[str] = []
        for label, items in (("GAINERS", gainers), ("LOSERS", losers)):
            for it in items:
                symbol = (it.get("symbol") or it.get("name") or "?").upper()
                change = it.get("change_24h")
                if change is None and it.get("timeframe"):
                    change = it.get(f"change_{it.get('timeframe')}")
                lines.append(
                    f"{label}: {symbol} {_fmt_pct(change)} vol {_fmt_volume(it.get('volume_24h'))} "
                    f"mcap {_fmt_volume(it.get('market_cap'))}"
                )
        if lines:
            parts.append("TOP MOVERS (24h): " + " | ".join(lines[:8]))

    trending = context.get("trending") or []
    if trending:
        t_lines = []
        for t in trending[:6]:
            t_lines.append(
                f"{t.get('symbol') or '?'} {_fmt_pct(t.get('change_24h'))} "
                f"vol {_fmt_volume(t.get('volume_24h'))} mcap {_fmt_volume(t.get('market_cap'))} "
                f"(chain {t.get('chain') or 'multi'})"
            )
        parts.append("TRENDING TOKENS: " + " | ".join(t_lines))

    news = context.get("news") or []
    if news:
        n_lines = []
        for r in news[:6]:
            content = (r.get("content") or r.get("title") or "").strip()[:160]
            if content:
                n_lines.append(content)
        parts.append("TOP NEWS (RAG news_v2): " + " || ".join(n_lines))

    narratives = context.get("narratives") or []
    if narratives:
        nv_lines = []
        for hit in narratives[:8]:
            label = f"[{hit.get('query')}]" if hit.get("query") else ""
            title = str(hit.get("title") or "").strip()[:120]
            url = str(hit.get("url") or "").strip()
            snippet = str(hit.get("snippet") or "").strip()[:160]
            nv_lines.append(f'{label} "{title}" ({url}) {snippet}'.strip())
        parts.append("NARRATIVE SIGNALS (web search): " + " | ".join(nv_lines))

    kol_signals = context.get("kol_signals") or []
    if kol_signals:
        kol_lines = []
        for hit in kol_signals[:8]:
            label = f"[{hit.get('query')}]" if hit.get("query") else ""
            title = str(hit.get("title") or "").strip()[:120]
            url = str(hit.get("url") or "").strip()
            snippet = str(hit.get("snippet") or "").strip()[:160]
            preview = str(hit.get("page_preview") or "").strip()[:160]
            text = snippet or preview
            kol_lines.append(f'{label} "{title}" ({url}) {text}'.strip())
        parts.append("X/KOL SIGNALS (web search): " + " | ".join(kol_lines))

    return "\n".join(parts)


#: System prompt — editorial voice + Telegram HTML + grounding. Explicitly
#: forbids bot clichés. The section order is the brief spec: overview →
#: narrative → movers → news → sentiment → X/KOL signals → what-to-watch.
SYSTEM_PROMPT = """You are the Daily Market Brief writer for Rug Munch Intelligence (@CryptoRugMunch) — a sharp, specific crypto market analyst writing the morning intelligence brief for a degen + institutional audience.

VOICE (non-negotiable):
- Sharp and specific: real numbers, real names, real percentages — all and ONLY from the provided data. Evidence-first, no filler.
- Zero bot clichés: never say "stay safe out there", "NFA", "not financial advice", "to the moon", "dyor", "gm", "wagmi", "mooning", "wen lambo", "HODL". Never hype. Say what IS.
- Conversational newsletter tone, not a robot. Short punchy sentences. Traders read this in 2 minutes.

OUTPUT FORMAT — Telegram HTML, in this EXACT section order:
<b>MARKET OVERVIEW</b>
One line: BTC/ETH/SOL prices with 24h % from the data. Then 1-2 sentences on the tone (risk-on/off, breadth, global mcap) using ONLY the provided verdict/reasons.
<b>🧭 NARRATIVE</b>
1-2 sentences naming the day's dominant crypto narrative(s) (e.g. AI agents, RWA tokenization, memes, L2 wars, restaking) — synthesized ONLY from the NARRATIVE SIGNALS block. Name the theme + the token/sector carrying it, cite the signal source. If NARRATIVE SIGNALS is absent, OMIT this section entirely.
<b>TRENDING MOVERS</b>
Bullet lines: • SYMBOL +X% — why it moved (use the news/reasons context when it explains a mover; otherwise say what the data shows: volume, mcap). Include 3-5 movers with their real numbers.
<b>TOP NEWS</b>
3-5 bullets: • headline (source) — pick the stories that actually move markets today.
<b>SENTIMENT</b>
One line: Fear & Greed X/100 (classification) + the market verdict with 1 reason.
<b>📣 X / KOL SIGNALS</b>
2-4 bullet lines: • X is talking about $TOKEN because <real, specific reason> — (source link). Use ONLY the X/KOL SIGNALS block: real mentions with a real source URL per line. HONEST RULE: never invent a shill, a KOL name, or a tweet — if a signal has no usable source text, skip it. If X/KOL SIGNALS is absent, OMIT this section entirely.
<b>WHAT TO WATCH TODAY</b>
2-3 concrete, specific items (events, levels, tokens) grounded in the data — not generic advice.

HARD RULES:
- Telegram HTML only: <b> and <i> tags, line breaks. No markdown, no backticks, no ``` fences, no <h1>, no <p>, no HTML boilerplate.
- ≤ 3500 characters total. Tight. No section may run long.
- Cite-or-omit: if a number is not in the provided data, do NOT invent it. Empty sections render as "N/A".
- No emoji spam: at most one emoji in the opening line (e.g. 📊). Section headers use exactly the tags above (🧭 NARRATIVE, 📣 X / KOL SIGNALS) — no other emojis."""


def build_messages(context: dict[str, Any]) -> list[dict[str, str]]:
    """Messages for ``chat_with_fallback(messages, "long")``.

    Pure — deterministic from ``context`` (a ``generated_at`` date line is
    stamped from the context so tests stay deterministic).
    """
    date_str = "—"
    try:
        generated = context.get("generated_at")
        if generated:
            date_str = datetime.fromisoformat(str(generated)).astimezone(UTC).strftime("%A, %B %d, %Y")
    except (TypeError, ValueError):
        date_str = "—"
    user_prompt = (
        f"Write today's Daily Market Brief for {date_str}.\n\n"
        "Here is the ONLY data you may use (cite-or-omit):\n"
        f"{_serialize_context(context)}\n\n"
        "Now write the report. Telegram HTML, sections in the exact order above, ≤3500 chars."
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


# ── Cleanup (pure) — markdown-ish LLM output → safe Telegram HTML ──────────

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*(?!\*)", re.DOTALL)
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_HEADER_RE = re.compile(r"^\s{0,3}#{1,4}\s+(.+)$", re.MULTILINE)
_ALLOWED_TAG_RE = re.compile(r"</?(?:b|i|a(?:\s+href=\"[^\"]*\")?)>")
_CODE_FENCE_RE = re.compile(r"```[a-zA-Z]*\n?|```")
_CODE_INLINE_RE = re.compile(r"`([^`]+)`")
_PLACEHOLDER = "__RMI_TAG_%d__"


def cleanup_html(text: str, *, max_chars: int = DAILY_MARKET_SUMMARY_MAX_CHARS) -> str:
    """Normalize LLM output to safe Telegram HTML and enforce the char budget.

    Steps (pure):
      1. Drop code fences + convert inline code to plain text.
      2. Convert markdown-ish ``#`` headers → ``<b>…</b>``, ``**bold**`` →
         ``<b>…</b>``, ``*italic*`` → ``<i>…</i>``.
      3. Escape ``& < >`` outside the allowlisted ``<b>``/``<i>``/``<a href>``
         tags (placeholder round-trip so existing tags survive).
      4. Trim to ``max_chars`` at a line boundary; never split a tag.

    Returns "" when the input is empty/whitespace-only (callers skip).
    """
    if not text or not text.strip():
        return ""
    out = _CODE_FENCE_RE.sub("", text)
    out = _CODE_INLINE_RE.sub(r"\1", out)
    out = _HEADER_RE.sub(r"<b>\1</b>", out)
    out = _BOLD_RE.sub(r"<b>\1</b>", out)
    out = _ITALIC_RE.sub(r"<i>\1</i>", out)

    # Placeholder round-trip: keep allowlisted tags, escape everything else.
    # ``seen`` maps a tag → its own stable index so repeated tags (e.g. two
    # ``<b>``) restore to the correct placeholder every time.
    placeholders: dict[int, str] = {}
    seen_tags: dict[str, int] = {}

    def _hold(match: re.Match[str]) -> str:
        tag = match.group(0)
        idx = seen_tags.get(tag)
        if idx is None:
            idx = len(seen_tags)
            seen_tags[tag] = idx
            placeholders[idx] = tag
        return _PLACEHOLDER % idx

    out = _ALLOWED_TAG_RE.sub(_hold, out)
    out = out.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for idx, tag in placeholders.items():
        out = out.replace(_PLACEHOLDER % idx, tag)

    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    if len(out) <= max_chars:
        return out

    # Trim to the budget at a line boundary (never mid-tag: we cut at \n).
    cut = out[:max_chars]
    newline = cut.rfind("\n")
    if newline > max_chars * 0.6:
        cut = cut[:newline]
    cut = cut.rstrip()
    if cut.endswith("<"):
        cut = cut[:-1]
    return cut + "…"


# ── rugmunch.io feed publish (fail-open) ────────────────────────────────────


async def publish_to_feed(report: str, *, title: str | None = None) -> dict[str, Any]:
    """Publish the brief to the rugmunch.io feed (Ghost content platform).

    The rugmunch.io social feed is backed by the Ghost content platform
    (``app.content_platform.py`` — "@CryptoRugMunch Feed engine"); the same
    ``create_post`` path the Morning Brief uses (``post_type="briefing"``).
    Fail-open: missing session cookie, network errors, and Ghost HTTP errors
    all log and return — the Telegram post is never blocked.
    """
    stamp = datetime.now(UTC).strftime("%B %d, %Y")
    try:
        from app.content_platform import create_post

        return await create_post(
            content=report,
            post_type="briefing",
            title=title or f"Daily Market Brief — {stamp}",
            tags=["daily", "market-summary"],
        )
    except Exception as exc:  # fail-open — feed publish is additive
        logger.warning("daily_market_summary:feed_publish_failed err=%s", str(exc)[:200])
        return {"error": str(exc)}


# ── Consumer (the scheduled job) ───────────────────────────────────────────


async def post_daily_market_summary(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Gather → LLM-write → clean → post to @cryptorugmuncher + feed.

    Returns 1 when the Telegram post shipped, 0 otherwise. Fails open at every
    step: missing chat id, no data, LLM failure, Telegram send error, or feed
    publish error all log and return without raising.
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from app.domains.telegram.rugmunchbot.channels.scheduler import _chat_id, _safe_send

    chat_id = _chat_id("main")  # registry ``main`` → @cryptorugmuncher
    if not chat_id:
        logger.info("daily_market_summary:no_main_chat_id_skipped")
        return 0

    context = await gather_summary_data()
    if not context.get("sources"):
        logger.info("daily_market_summary:no_data_sources_skipped")
        return 0

    messages = build_messages(context)
    try:
        from app.llm_config import chat_with_fallback

        raw = await chat_with_fallback(messages, "long")
    except Exception as exc:  # fail-open — a broken LLM never crashes the tick
        logger.warning("daily_market_summary:llm_failed err=%s", str(exc)[:160])
        raw = ""
    if not raw or not raw.strip():
        logger.info("daily_market_summary:llm_empty_skipped sources=%s", context.get("sources"))
        return 0

    report = cleanup_html(raw)
    if not report:
        logger.info("daily_market_summary:cleaned_empty_skipped")
        return 0
    logger.info(
        "daily_market_summary:report_ready chars=%s sources=%s",
        len(report),
        context.get("sources"),
    )

    ok = await _safe_send(ctx.bot, chat_id, report)
    if not ok:
        return 0
    logger.info("daily_market_summary:posted_main chars=%s", len(report))

    if DAILY_MARKET_SUMMARY_FEED_ENABLED:
        await publish_to_feed(report)
    return 1


__all__ = [
    "DAILY_MARKET_SUMMARY_ENABLED",
    "DAILY_MARKET_SUMMARY_FEED_ENABLED",
    "DAILY_MARKET_SUMMARY_MAX_CHARS",
    "DAILY_MARKET_SUMMARY_TIME",
    "DAILY_MARKET_SUMMARY_TZ",
    "DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED",
    "SYSTEM_PROMPT",
    "build_messages",
    "cleanup_html",
    "gather_summary_data",
    "post_daily_market_summary",
    "publish_to_feed",
    "summary_run_time",
]
