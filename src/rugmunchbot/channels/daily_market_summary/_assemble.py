from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.channels.daily_market_summary._config import (
    DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED,
)
from rugmunchbot.channels.daily_market_summary._fetch import (
    _fetch_global_metrics,
    _fetch_kol_signals,
    _fetch_major_prices,
    _fetch_market_condition,
    _fetch_movers,
    _fetch_narratives,
    _fetch_news,
    _fetch_trending,
)
from rugmunchbot.channels.daily_market_summary._fmt import (
    _fmt_pct,
    _fmt_price,
    _fmt_volume,
)

logger = logging.getLogger(__name__)


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
    prices, condition, trending, movers, news, global_metrics = await asyncio.gather(
        _fetch_major_prices(),
        _fetch_market_condition(),
        _fetch_trending(),
        _fetch_movers(),
        _fetch_news(),
        _fetch_global_metrics(),
        return_exceptions=True,
    )
    blocks = [prices, condition, trending, movers, news, global_metrics]
    cleaned = [b if not isinstance(b, BaseException) else {} for b in blocks]

    # Dune on-chain digest + social composite — new intel lanes (2026-08-23).
    dune_data: dict[str, Any] = {}
    social_data: dict[str, Any] = {}
    try:
        from rugmunchbot.tools.dune_intel import dune_intel

        _d = await dune_intel(limit=3)
        if _d.ok:
            dune_data = (_d.data or {}).get("digest") or {}
    except Exception as exc:
        logger.debug("daily_rug:dune_failed err=%s", str(exc)[:120])
    try:
        from rugmunchbot.tools.social_intel import social_intel as _social_intel

        _s = await _social_intel(symbol="BTC")
        if _s.ok:
            social_data = _s.data or {}
    except Exception as exc:
        logger.debug("daily_rug:social_failed err=%s", str(exc)[:120])

    # The Daily Rug doctrine: no lies survive to publication. Symbol-colliding
    # impostor pairs ("SOL" the $20M meme) and absurd volume prints are
    # rejected here with logged reasons.
    from rugmunchbot.channels.daily_market_summary._validate import (
        sanitize_movers,
        sanitize_prices,
    )

    cleaned[0] = sanitize_prices(cleaned[0])
    cleaned[3] = sanitize_movers(cleaned[3])
    sources = ["prices", "condition", "trending", "movers", "news", "global_metrics"]
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

    # The Daily Rug story selection: freshness gate -> 4B reranker ranking.
    # Candidates = RAG news + web narratives; only market-moving, dated,
    # <48h stories survive. Fail-open at every stage.
    try:
        from rugmunchbot.channels.daily_market_summary._rerank import (
            enforce_freshness,
            rerank_stories,
        )

        candidates = [s for s in (cleaned[4] if isinstance(cleaned[4], list) else []) if isinstance(s, dict)]
        candidates += [n for n in narratives if isinstance(n, dict)]
        fresh, _rej = enforce_freshness(candidates)
        top_stories, _ranked = await rerank_stories(fresh, top_n=6)
    except Exception as exc:  # selection must never kill the summary
        logger.warning("daily_rug:selection_failed fail_open err=%s", str(exc)[:140])
        top_stories = cleaned[4] if isinstance(cleaned[4], list) else []

    return {
        "prices": cleaned[0] if isinstance(cleaned[0], dict) else {},
        "condition": cleaned[1] if isinstance(cleaned[1], dict) else {},
        "trending": cleaned[2] if isinstance(cleaned[2], list) else [],
        "movers": cleaned[3] if isinstance(cleaned[3], dict) else {},
        "news": top_stories,
        "global_metrics": cleaned[5] if isinstance(cleaned[5], dict) else {},
        "narratives": narratives,
        "kol_signals": kol_signals,
        "dune_intel": dune_data,
        "social_intel": social_data,
        "sources": active,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }


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
            parts.append(
                f"FEAR & GREED: {fg.get('value')}/100 ({fg.get('classification') or 'N/A'})"
            )
        verdict = condition.get("verdict")
        if verdict:
            parts.append(
                f"MARKET VERDICT: {verdict} (confidence {condition.get('confidence') or 'low'})"
            )
        reasons = condition.get("reasons") or []
        if reasons:
            parts.append("VERDICT REASONS: " + " | ".join(str(r) for r in reasons[:4]))
        mcap = signals.get("global_market_cap_usd")
        mcap_chg = signals.get("global_market_cap_change_24h")
        if mcap is not None or mcap_chg is not None:
            parts.append(f"GLOBAL MCAP: {_fmt_volume(mcap)} ({_fmt_pct(mcap_chg)})")

    global_metrics = context.get("global_metrics") or {}
    if global_metrics:
        g_lines = []
        if global_metrics.get("total_mcap") is not None:
            g_lines.append(f"total mcap {_fmt_volume(global_metrics.get('total_mcap'))}")
        if global_metrics.get("total_volume_24h") is not None:
            g_lines.append(f"24h volume {_fmt_volume(global_metrics.get('total_volume_24h'))}")
        if global_metrics.get("btc_dominance") is not None:
            g_lines.append(f"BTC dominance {global_metrics.get('btc_dominance'):.1f}%")
        if global_metrics.get("eth_dominance") is not None:
            g_lines.append(f"ETH dominance {global_metrics.get('eth_dominance'):.1f}%")
        if global_metrics.get("mcap_change_24h") is not None:
            g_lines.append(f"mcap 24h {_fmt_pct(global_metrics.get('mcap_change_24h'))}")
        if g_lines:
            parts.append("GLOBAL METRICS: " + " · ".join(g_lines))

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
            url = str(r.get("canonical_url") or "").strip()
            if content:
                n_lines.append(f"{content} [url={url}]" if url else content)
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
