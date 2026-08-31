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

#: RAG collections queried for the brief's intel context (rugbrain news_v2
#: + threat_intel_v2 — market events, alerts, shill campaigns, last 24h).
_RAG_INTEL_COLLECTIONS = ["news_v2", "threat_intel_v2"]
_RAG_INTEL_QUERY = (
    "crypto market events alerts shill campaigns exploits hacks rug warnings last 24 hours"
)
_RAG_INTEL_LIMIT = 4  # per collection — dedup keeps the prompt small
_RAG_INTEL_SNIPPET_MAX = 300


async def _fetch_rag_intel() -> list[dict[str, Any]]:
    """Query rugbrain news_v2 + threat_intel_v2 for last-24h intel context.

    Reuses the bot-side direct-Qdrant RAG lane (``rag_lookup._rag_direct_qdrant``
    — one embed + per-collection vector search, ~1-3s total) so the brief's
    commentary is grounded in RAG-served intel the same way ``chat_ai`` is.
    Returns raw hits (deduped by content prefix) or ``[]`` — never raises.
    """
    from rugmunchbot.tools.rag_lookup import _rag_direct_qdrant, _rag_direct_search_multi

    query = _RAG_INTEL_QUERY
    hits = await _rag_direct_qdrant(query, _RAG_INTEL_COLLECTIONS, _RAG_INTEL_LIMIT)
    if not hits:
        # Fallback: rugbrain rag-api /search/multi (same lane chat_ai uses).
        hits = await _rag_direct_search_multi(query, _RAG_INTEL_COLLECTIONS, _RAG_INTEL_LIMIT * 2)
    if not hits:
        return []
    # Dedupe by normalized content prefix (chunked points share one story).
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
        if len(out) >= 6:
            break
    return out


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
        logger.debug("market_report:dune_failed err=%s", str(exc)[:120])
    try:
        from rugmunchbot.tools.social_intel import social_intel as _social_intel

        _s = await _social_intel(symbol="BTC")
        if _s.ok:
            social_data = _s.data or {}
    except Exception as exc:
        logger.debug("market_report:social_failed err=%s", str(exc)[:120])

    # RugBrain intel lanes — whales/exploits/sentiment/regulation from the
    # standalone market-awareness collections (2026-08-25). Fail-open.
    rugbrain_intel: dict[str, list[dict[str, Any]]] = {}
    try:
        from rugmunchbot.channels.daily_market_summary._fetch import (
            _fetch_rugbrain_intel,
        )

        rugbrain_intel = await _fetch_rugbrain_intel()
    except Exception as exc:
        logger.debug("market_report:intel_failed err=%s", str(exc)[:120])

    # RAG intel lane — rugbrain news_v2 + threat_intel_v2 (market events,
    # alerts, shill campaigns in the last 24h) so the commentary references
    # RAG-served intel, not just raw price data (2026-08-31). Fail-open.
    rag_intel: list[dict[str, Any]] = []
    try:
        rag_intel = await _fetch_rag_intel()
    except Exception as exc:
        logger.debug("market_report:rag_intel_failed err=%s", str(exc)[:120])

    # Market-report doctrine: no lies survive to publication. Symbol-colliding
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
    if rugbrain_intel:
        active.append("rugbrain_intel")
    if rag_intel:
        active.append("rag_intel")

    # Market-report story selection: freshness gate -> 4B reranker ranking.
    # Candidates = RAG news + web narratives; only market-moving, dated,
    # <48h stories survive. Fail-open at every stage.
    try:
        from rugmunchbot.channels.daily_market_summary._rerank import (
            enforce_freshness,
            rerank_stories,
        )

        candidates = [
            s for s in (cleaned[4] if isinstance(cleaned[4], list) else []) if isinstance(s, dict)
        ]
        candidates += [n for n in narratives if isinstance(n, dict)]
        fresh, _rej = enforce_freshness(candidates)
        top_stories, _ranked = await rerank_stories(fresh, top_n=6)
    except Exception as exc:  # selection must never kill the summary
        logger.warning("market_report:selection_failed fail_open err=%s", str(exc)[:140])
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
        "rugbrain_intel": rugbrain_intel,
        "rag_intel": rag_intel,
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

    dune = context.get("dune_intel") or {}
    d_queries = dune.get("queries") or {}
    if d_queries:
        d_lines = []
        for name, q in list(d_queries.items())[:4]:
            if "error" in q:
                continue
            rows = q.get("results") or q.get("data") or []
            if rows and isinstance(rows[0], dict):
                kv = ", ".join(f"{k}={v}" for k, v in list(rows[0].items())[:3])
                d_lines.append(f"{name}: {kv}")
        if d_lines:
            parts.append("ON-CHAIN ANALYTICS (Dune, real queries): " + " | ".join(d_lines))

    social = context.get("social_intel") or {}
    if social.get("narrative"):
        s_bits = [
            f"velocity={social.get('mention_velocity')}",
            f"mentions24h={social.get('mentions_24h')}",
        ]
        if social.get("sentiment_lean") is not None:
            s_bits.append(f"lean={social['sentiment_lean']:+.0f}")
        parts.append("SOCIAL PULSE (BTC): " + ", ".join(s_bits))

    # RugBrain intel lanes (whales/exploits/sentiment/regulation) — cite-or-omit.
    intel = context.get("rugbrain_intel") or {}
    intel_lines: list[str] = []
    for lane, label in (
        ("whales", "WHALES"),
        ("exploits", "EXPLOITS"),
        ("sentiment", "SENTIMENT"),
        ("regulation", "REGULATORS"),
    ):
        for item in (intel.get(lane) or [])[:3]:
            intel_lines.append(f"[{label}] {item.get('content', '')}")
    if intel_lines:
        parts.append("RUGBRAIN INTEL (from live collections): " + " | ".join(intel_lines[:10]))

    # RAG intel (news_v2 + threat_intel_v2, last 24h) — cite-or-omit.
    rag_intel = context.get("rag_intel") or []
    if rag_intel:
        r_lines = []
        for hit in rag_intel[:6]:
            content = str(hit.get("content") or "").strip()[:_RAG_INTEL_SNIPPET_MAX]
            col = str(hit.get("collection") or "rag")
            if content:
                r_lines.append(f"[{col}] {content}")
        if r_lines:
            parts.append("RAG INTEL (news_v2 + threat_intel_v2, last 24h): " + " | ".join(r_lines))
    return "\n".join(parts)
