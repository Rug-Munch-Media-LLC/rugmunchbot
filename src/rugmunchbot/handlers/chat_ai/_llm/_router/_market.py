"""MARKET-TODAY rich context (broad market asks) + shared narrative fetch."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from rugmunchbot.handlers.chat_ai import _llm as _llm_mod
from rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    _CONTEXT_MAX_CHARS,
    _CTX_TA_RE,
    _CTX_TRENDING_RE,
    _MARKET_ANSWER_HINT,
    _MARKET_CACHE_KEY,
    _MARKET_CACHE_TTL,
    _MARKET_MOVERS_DEFAULT,
    _MARKET_NEWS_LIMIT,
    _TRENDING_ANSWER_HINT,
    _TRENDING_CONTEXT_MAX_CHARS,
    _TRENDING_FETCH_LIMIT,
    _WEB_FALLBACK_TIMEOUT,
)
from rugmunchbot.handlers.chat_ai._llm._router._routing import _requested_token_count
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.intent import _fmt_signal

logger = get_logger("rmi_bot.chat_ai")


async def _fetch_market_narrative() -> list[dict[str, Any]]:
    """Dominant-narrative hits (SearXNG) for the market-today block.

    Mirrors the daily brief's narrative fetch — same thematic queries and the
    same ``tools.web.searxng_top_results`` tool (no new service) — but
    bounded to the router budget: the first two queries run concurrently and
    whatever finishes inside the cap contributes; a slow query can never
    starve the market block. Fail-open → ``[]``.
    """
    try:
        from rugmunchbot.tools.web import NARRATIVE_QUERIES, searxng_top_results

        queries = list(NARRATIVE_QUERIES)[:2]
        tasks = [asyncio.create_task(searxng_top_results(q, limit=3)) for q in queries]
        try:
            done, _pending = await asyncio.wait(tasks, timeout=_WEB_FALLBACK_TIMEOUT)
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        out: list[dict[str, Any]] = []
        for query, t in zip(queries, tasks, strict=True):
            if t not in done or t.cancelled():
                continue
            try:
                res = t.result()
            except Exception as exc:
                logger.debug("question-router narrative query failed: %s", exc)
                continue
            for r in res or []:
                if isinstance(r, dict):
                    hit = dict(r)
                    hit["query"] = query
                    out.append(hit)
        return out[:6]
    except Exception as exc:
        logger.debug("question-router narrative fetch failed: %s", exc)
    return []


async def _fetch_news_headlines(limit: int = _MARKET_NEWS_LIMIT) -> list[dict[str, str]]:
    """Recent news headlines from the backend (fail-open → ``[]``).

    ``GET /api/v1/news/headlines?limit=10`` returns
    ``{"headlines": [{title, url, source, ...}], "count": N}``. Only real
    titles survive; a backend miss degrades to ``[]`` so the market block
    simply omits the news section (never invents a headline).
    """
    try:
        from rugmunchbot.tools.registry import backend_json_get

        body = await backend_json_get(
            "/api/v1/news/headlines", params={"limit": limit}, timeout=4.0
        )
    except Exception as exc:
        logger.debug("question-router market news fetch failed: %s", exc)
        return []
    if not isinstance(body, dict):
        return []
    headlines: list[dict[str, str]] = []
    for it in (body.get("headlines") or [])[:limit]:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        headlines.append(
            {
                "title": title[:120],
                "url": str(it.get("url") or "").strip(),
                "source": str(it.get("source") or "").strip(),
            }
        )
    return headlines


async def _fetch_backend_market_overview() -> dict[str, Any] | None:
    """Backend market overview (fail-open → ``None``).

    ``GET /api/v1/market/overview`` returns global market cap / 24h volume /
    BTC-ETH dominance / BTC-ETH price + 24h change / Fear & Greed. A second
    live source for the market block — it keeps the block populated with real
    market data even when the ``market_overview`` tool (CoinGecko) is down. A
    miss degrades to ``None``.
    """
    try:
        from rugmunchbot.tools.registry import backend_json_get

        body = await backend_json_get("/api/v1/market/overview", timeout=4.0)
    except Exception as exc:
        logger.debug("question-router market overview fetch failed: %s", exc)
        return None
    return body if isinstance(body, dict) else None


async def _market_cache_get() -> tuple[str, list[dict[str, str]]] | None:
    """Read the cached market-today block (5 min). Fail-open → ``None``."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is None:
            return None
        raw = await r.get(_MARKET_CACHE_KEY)
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        block = data.get("block")
        sources = data.get("sources")
        if not isinstance(block, str) or not block:
            return None
        return block, sources if isinstance(sources, list) else []
    except Exception as exc:
        logger.debug("question-router market cache read failed: %s", exc)
        return None


async def _market_cache_set(block: str, sources: list[dict[str, str]]) -> None:
    """Write the assembled market-today block to Redis (5 min). Fail-open."""
    if not block:
        return
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is None:
            return
        await r.set(
            _MARKET_CACHE_KEY,
            json.dumps({"block": block, "sources": sources}),
            ex=_MARKET_CACHE_TTL,
        )
    except Exception as exc:
        logger.debug("question-router market cache write failed: %s", exc)


def _extract_majors(results: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Normalize BTC/ETH/SOL majors (price + 24h change) from live payloads.

    Prefers ``market_overview`` prices (has SOL); falls back to
    ``market_condition`` signals (BTC/ETH) when the overview missed. Only
    real values are kept — a missing coin stays absent (renders as
    "unavailable", never invented).
    """
    out: dict[str, dict[str, Any]] = {}
    overview = results.get("market_overview")
    if overview is not None and getattr(overview, "ok", False) and isinstance(overview.data, dict):
        prices = overview.data.get("prices") or {}
        if isinstance(prices, dict):
            for key, sym in (("bitcoin", "BTC"), ("ethereum", "ETH"), ("solana", "SOL")):
                p = prices.get(key)
                if isinstance(p, dict) and p.get("usd") is not None:
                    out[sym] = {
                        "symbol": sym,
                        "price_usd": p.get("usd"),
                        "change_24h": p.get("usd_24h_change"),
                        "source": "backend:/api/v1/databus/market/prices",
                    }
    condition = results.get("market_condition")
    if (
        condition is not None
        and getattr(condition, "ok", False)
        and isinstance(condition.data, dict)
    ):
        signals = condition.data.get("signals") or {}
        if isinstance(signals, dict):
            for key, sym in (("btc", "BTC"), ("eth", "ETH")):
                if sym in out:
                    continue
                s = signals.get(key)
                if isinstance(s, dict) and s.get("price_usd") is not None:
                    out[sym] = {
                        "symbol": sym,
                        "price_usd": s.get("price_usd"),
                        "change_24h": s.get("change_24h"),
                        "source": s.get("source") or "live",
                    }
    return out


def _movers_from_trending(
    results: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the trending feed into top gainers / top losers (24h change).

    Uses ONLY tokens with a real ``change_24h`` (never invented). Returns
    ``([], [])`` when the trending tool missed or the feed carries no
    changes.
    """
    trending_res = results.get("trending")
    if (
        trending_res is None
        or not getattr(trending_res, "ok", False)
        or not isinstance(trending_res.data, dict)
    ):
        return [], []
    tokens = [t for t in (trending_res.data.get("tokens") or []) if isinstance(t, dict)]
    with_change = [t for t in tokens if isinstance(t.get("change_24h"), (int, float))]
    if not with_change:
        return [], []
    ranked = sorted(with_change, key=lambda t: float(t["change_24h"]), reverse=True)
    gainers = [t for t in ranked if float(t["change_24h"]) > 0]
    losers = [t for t in ranked[::-1] if float(t["change_24h"]) < 0]
    return gainers, losers


def _mover_label(token: dict[str, Any]) -> str:
    """Render one mover: '$SYM +45.2%' (+ name when distinct)."""
    sym = str(token.get("symbol") or "").strip().upper()
    name = str(token.get("name") or "").strip()
    chg = token.get("change_24h")
    pct = _fmt_signal("change_24h", chg) if isinstance(chg, (int, float)) else "n/a"
    label = f"${sym} {pct}" if sym else f"{name or 'token'} {pct}"
    if name and name.upper() != sym:
        label += f" ({name})"
    return label


def _mover_driver(token: dict[str, Any], narratives: list[dict[str, Any]]) -> str:
    """One-line driver when the daily-brief narrative supports the mover.

    Matches the token's symbol/name against the narrative hits (title +
    snippet); returns ``''`` when nothing ties — the mover renders with its
    % only, never an invented reason.
    """
    sym = str(token.get("symbol") or "").strip().upper()
    name = str(token.get("name") or "").strip()
    if not sym and not name:
        return ""
    for n in narratives:
        if not isinstance(n, dict):
            continue
        text = f"{n.get('title') or ''} {n.get('snippet') or ''}".lower()
        matched = (sym and sym.lower() in text) or (name and name.lower() in text)
        if not matched:
            continue
        title = str(n.get("title") or "narrative").strip()[:40]
        url = str(n.get("url") or "").strip()
        return f" — narrative ties it to '{title}'" + (f" ({url})" if url else "")
    return ""


def _what_to_watch_seed(results: dict[str, Any], narratives: list[dict[str, Any]]) -> list[str]:
    """Concrete what-to-watch seeds derived ONLY from the real data above."""
    seeds: list[str] = []
    condition = results.get("market_condition")
    if (
        condition is not None
        and getattr(condition, "ok", False)
        and isinstance(condition.data, dict)
    ):
        data = condition.data
        verdict = data.get("verdict") or "neutral"
        score = data.get("score")
        score_txt = f"{score:+.2f}" if isinstance(score, (int, float)) else "n/a"
        seeds.append(
            f"market-condition verdict is {verdict} (score {score_txt}) — watch whether momentum confirms or flips"
        )
        breadth = (data.get("signals") or {}).get("breadth")
        if (
            isinstance(breadth, dict)
            and isinstance(breadth.get("pumping"), int)
            and isinstance(breadth.get("dumping"), int)
        ):
            seeds.append(
                f"breadth is {breadth.get('pumping')} up / {breadth.get('dumping')} down in our trending feed — watch for breadth confirmation"
            )
    majors = _extract_majors(results)
    btc = majors.get("BTC")
    if btc and isinstance(btc.get("price_usd"), (int, float)):
        price = _fmt_signal("price_usd", btc["price_usd"])
        chg = btc.get("change_24h")
        chg_txt = _fmt_signal("change_24h", chg) if isinstance(chg, (int, float)) else ""
        seeds.append(
            f"BTC at {price}{f' ({chg_txt} 24h)' if chg_txt else ''} — watch the level hold/break"
        )
    for n in narratives[:1]:
        if isinstance(n, dict) and n.get("title"):
            seeds.append(f"watch the '{n.get('title')}' narrative for follow-through")
    return seeds[:3]


def _build_market_today_block(
    results: dict[str, Any],
    narratives: list[dict[str, Any]],
    mover_cap: int = _MARKET_MOVERS_DEFAULT,
    news: list[dict[str, str]] | None = None,
    backend_overview: dict[str, Any] | None = None,
) -> str:
    """Assemble the rich market-today context block from real tool payloads.

    Pure function (no I/O): merges the market_condition verdict + Fear&Greed,
    market_overview majors (BTC/ETH/SOL price + 24h) + global mcap/dominance,
    trending tokens (top gainers AND losers with % and a narrative-backed
    driver when supported), the daily-brief narrative hits, recent news
    headlines (``news``) and the backend market overview (``backend_overview``)
    plus a what-to-watch seed. ``mover_cap`` is the per-side cap (default 3); a
    count-bearing ask raises it so gainers + losers surface ~20 tokens total.
    Every line carries ONLY numbers present in the payloads — a missing
    signal renders as ``unavailable``, never invented. ``results`` is the
    ``{name: ToolResult}`` dict from the registry; ``narratives`` the
    ``{title, url, snippet, query}`` hits from the daily-brief narrative
    fetch; ``news`` the ``{title, url, source}`` backend headlines;
    ``backend_overview`` the raw ``/api/v1/market/overview`` dict.
    """
    lines: list[str] = ["LIVE MARKET DATA (real, fetched just now):"]
    _add = lines.append

    majors = _extract_majors(results)
    if majors:
        # 7d price change is NOT provided by any live tool — the block says
        # so explicitly (requirement: name the specific missing field, never
        # a blanket no-live-data disclaimer).
        _add("## MAJORS (24h change; 7d change unavailable from live sources)")
        for sym in ("BTC", "ETH", "SOL"):
            m = majors.get(sym)
            if not m:
                _add(f"- {sym}: price/change unavailable")
                continue
            price = _fmt_signal("price_usd", m.get("price_usd"))
            chg = m.get("change_24h")
            chg_txt = _fmt_signal("change_24h", chg) if isinstance(chg, (int, float)) else "n/a"
            _add(f"- {sym} {price} ({chg_txt} 24h)")

    condition = results.get("market_condition")
    if (
        condition is not None
        and getattr(condition, "ok", False)
        and isinstance(condition.data, dict)
    ):
        data = condition.data
        _add("## SENTIMENT & MACRO READ")
        fg = (data.get("signals") or {}).get("fear_greed")
        if isinstance(fg, dict) and isinstance(fg.get("value"), (int, float)):
            _add(f"- Fear & Greed: {fg['value']:.0f} ({fg.get('classification') or 'n/a'})")
        else:
            _add("- Fear & Greed: unavailable")
        verdict = data.get("verdict") or "neutral"
        score = data.get("score")
        score_txt = f"{score:+.2f}" if isinstance(score, (int, float)) else "n/a"
        _add(
            f"- Market condition: {verdict} (score {score_txt}, {data.get('confidence') or 'low'} confidence)"
        )
        for reason in (data.get("reasons") or [])[:4]:
            if isinstance(reason, str) and reason.strip():
                _add(f"  * {reason.strip()}")
    else:
        # market_condition missed → fall back to market_overview's F&G only
        # (the verdict is genuinely unavailable — say so, never guess).
        overview = results.get("market_overview")
        if (
            overview is not None
            and getattr(overview, "ok", False)
            and isinstance(overview.data, dict)
        ):
            fg = overview.data.get("fear_greed")
            if isinstance(fg, dict) and fg.get("value") is not None:
                _add("## SENTIMENT & MACRO READ")
                _add(f"- Fear & Greed: {fg.get('value')} ({fg.get('classification') or 'n/a'})")
                _add("- Market-condition verdict: unavailable (market_condition source down)")

    overview = results.get("market_overview")
    if overview is not None and getattr(overview, "ok", False) and isinstance(overview.data, dict):
        od = overview.data
        if isinstance(od.get("global_market_cap_usd"), (int, float)):
            _add(f"- Global market cap: {_fmt_signal('market_cap', od['global_market_cap_usd'])}")
        if isinstance(od.get("btc_dominance"), (int, float)):
            _add(f"- BTC dominance: {od['btc_dominance']:.1f}%")
        if isinstance(od.get("global_volume_24h_usd"), (int, float)):
            _add(f"- 24h volume: {_fmt_signal('volume_24h', od['global_volume_24h_usd'])}")

    # Backend /api/v1/market/overview — a second live source for the global
    # picture (market cap / volume / dominance / BTC-ETH price + change).
    # Rendered only when the tool's market_overview missed, so the block never
    # duplicates the same metric twice.
    if backend_overview and not (overview is not None and getattr(overview, "ok", False)):
        _add("## GLOBAL MARKET (backend /api/v1/market/overview)")
        if isinstance(backend_overview.get("total_market_cap"), (int, float)):
            _add(
                f"- Global market cap: {_fmt_signal('market_cap', backend_overview['total_market_cap'])}"
            )
        if isinstance(backend_overview.get("total_volume_24h"), (int, float)):
            _add(f"- 24h volume: {_fmt_signal('volume_24h', backend_overview['total_volume_24h'])}")
        if isinstance(backend_overview.get("btc_dominance"), (int, float)):
            _add(f"- BTC dominance: {backend_overview['btc_dominance']:.1f}%")
        for sym, price_key, chg_key in (
            ("BTC", "btc_price", "btc_change_24h"),
            ("ETH", "eth_price", "eth_change_24h"),
        ):
            price = backend_overview.get(price_key)
            if isinstance(price, (int, float)):
                chg = backend_overview.get(chg_key)
                chg_txt = _fmt_signal("change_24h", chg) if isinstance(chg, (int, float)) else "n/a"
                _add(f"- {sym} {_fmt_signal('price_usd', price)} ({chg_txt} 24h)")

    gainers, losers = _movers_from_trending(results)
    if gainers:
        _add("## TOP GAINERS (24h)")
        for t in gainers[:mover_cap]:
            _add(f"- {_mover_label(t)}{_mover_driver(t, narratives)}")
    if losers:
        _add("## TOP LOSERS (24h)")
        for t in losers[:mover_cap]:
            _add(f"- {_mover_label(t)}{_mover_driver(t, narratives)}")
    if not gainers and not losers:
        _add("## MOVERS — unavailable (no live trending feed)")

    if news:
        _add("## RECENT NEWS HEADLINES (backend /api/v1/news/headlines)")
        for n in news[:_MARKET_NEWS_LIMIT]:
            title = str(n.get("title") or "").strip()[:120]
            if not title:
                continue
            url = str(n.get("url") or "").strip()
            src = str(n.get("source") or "").strip()
            line = f"- {title}"
            if src:
                line += f" ({src})"
            if url:
                line += f" — {url}"
            _add(line)

    if narratives:
        _add("## DOMINANT NARRATIVE (daily-brief narrative fetch)")
        for n in narratives[:3]:
            if not isinstance(n, dict):
                continue
            title = str(n.get("title") or "narrative").strip()[:80]
            snippet = str(n.get("snippet") or "").strip()[:160]
            url = str(n.get("url") or "").strip()
            line = f"- {title}"
            if snippet:
                line += f": {snippet}"
            if url:
                line += f" ({url})"
            _add(line)
    else:
        _add(
            "## DOMINANT NARRATIVE — unavailable (narrative fetch returned nothing; omit this section)"
        )

    seeds = _what_to_watch_seed(results, narratives)
    if seeds:
        _add("## WHAT TO WATCH (seed — expand with the data above)")
        for s in seeds:
            _add(f"- {s}")

    lines.append(
        "You may ONLY cite symbols, prices and metrics that appear in this "
        "LIVE MARKET DATA. If a specific field is missing, say that field is "
        "unavailable — never hedge with a blanket 'no live data'."
    )
    return "\n".join(lines)


async def _gather_market_today_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """RICH market-today context: majors + sentiment + movers + narrative.

    For BROAD market asks ("market today", "market analysis", "how's the
    market", "top gainers and losers") runs the existing market tools
    CONCURRENTLY (``market_condition`` verdict + Fear&Greed,
    ``market_overview`` majors incl. SOL, ``trending`` movers) plus the
    daily-brief narrative fetch (SearXNG, fail-open), recent news headlines
    (backend ``/api/v1/news/headlines``) and the backend market overview
    (``/api/v1/market/overview``) and assembles the structured block —
    majors → sentiment/macro → gainers & losers (with narrative-backed
    drivers) → news → narrative → what-to-watch seed — with the MARKET-TODAY
    answer-structure hint appended (same pattern as the TA/EDU hints).

    The assembled block is cached in Redis for 5 minutes so repeated market
    questions answer fast without re-fetching every source. Fail-open: any
    tool / narrative / news / overview that misses simply doesn't contribute;
    when NO tool answered but news or the backend overview landed, the block
    is still built from that live data (never a bare timeout); only when
    EVERYTHING missed does the web fallback stand in. Never raises.
    """
    # Fast path: a market-today block assembled within the last 5 minutes is
    # served straight from Redis — repeated "market today" questions skip the
    # tool + news + narrative fetches entirely.
    cached = await _market_cache_get()
    if cached is not None:
        return cached

    from rugmunchbot.tools import get_registry
    from rugmunchbot.tools.intent import tool_kwargs

    names = ["market_condition", "market_overview", "trending"]
    if _CTX_TA_RE.search(question.lower()):
        names.append("technical_analysis")
    spec = tool_kwargs(question, names)
    # TRENDING/movers asks fetch ~20 candidates (the intent default is 10) so
    # the market block can surface up to ~20 movers when a count is requested.
    if spec.get("trending"):
        spec["trending"]["limit"] = _TRENDING_FETCH_LIMIT
    try:
        tools_task = asyncio.create_task(get_registry().run_many_kwargs(spec))
        narrative_task = asyncio.create_task(_fetch_market_narrative())
        news_task = asyncio.create_task(_fetch_news_headlines())
        overview_task = asyncio.create_task(_fetch_backend_market_overview())
    except Exception as exc:
        logger.debug("question-router market-today gather failed (query %.60s): %s", question, exc)
        return "", []
    try:
        # Run tools + narrative + news + overview CONCURRENTLY to the full
        # router budget; all get the whole window (no FIRST_COMPLETED
        # cancellation bug). A slow RAG/tool fetch can never starve the live
        # news/overview legs.
        await asyncio.wait(
            {tools_task, narrative_task, news_task, overview_task},
            timeout=_CONTEXT_GATHER_TIMEOUT,
            return_when=asyncio.ALL_COMPLETED,
        )
    except Exception as exc:
        logger.debug("question-router market-today gather failed (query %.60s): %s", question, exc)
    tools: dict[str, Any] = {}
    try:
        if not tools_task.cancelled() and tools_task.done():
            tools = tools_task.result() or {}
    except Exception as exc:
        logger.debug("question-router market-today tools failed (query %.60s): %s", question, exc)
    narratives: list[dict[str, Any]] = []
    try:
        if not narrative_task.cancelled() and narrative_task.done():
            narratives = narrative_task.result() or []
    except Exception as exc:
        logger.debug(
            "question-router market-today narrative failed (query %.60s): %s", question, exc
        )
    news: list[dict[str, str]] = []
    try:
        if not news_task.cancelled() and news_task.done():
            news = news_task.result() or []
    except Exception as exc:
        logger.debug("question-router market-today news failed (query %.60s): %s", question, exc)
    backend_overview: dict[str, Any] | None = None
    try:
        if not overview_task.cancelled() and overview_task.done():
            backend_overview = overview_task.result()
    except Exception as exc:
        logger.debug(
            "question-router market-today overview failed (query %.60s): %s", question, exc
        )
    finally:
        for t in (tools_task, narrative_task, news_task, overview_task):
            if not t.done():
                t.cancel()
        if any(t.cancelled() for t in (tools_task, narrative_task, news_task, overview_task)):
            await asyncio.gather(
                tools_task, narrative_task, news_task, overview_task, return_exceptions=True
            )

    ok_results = {name: r for name, r in tools.items() if getattr(r, "ok", False)}
    # Fail-open: even when every market tool missed, the live news headlines
    # and/or backend market overview still give the answer real data — build
    # the block from whatever landed instead of timing out. Only when NOTHING
    # landed do we fall through to the SearXNG web layer.
    if not ok_results and not news and not backend_overview:
        # All market tools + news + overview missed → CURRENT web results so
        # the answer still gets sourced context (fail-open: "" when SearXNG
        # is down too).
        return await _llm_mod._gather_web_fallback(question)
    try:
        # A count-bearing ask ("top 20 gainers") raises the per-side mover
        # cap toward _TRENDING_FETCH_LIMIT // 2 (~20 tokens total).
        mover_cap = min(
            _requested_token_count(question) or _MARKET_MOVERS_DEFAULT, _TRENDING_FETCH_LIMIT // 2
        )
        block = _build_market_today_block(
            ok_results,
            narratives,
            mover_cap=mover_cap,
            news=news,
            backend_overview=backend_overview,
        )
        # TRENDING-typed broad asks ("top gainers and losers today") get the
        # TRENDING hint (open "Here's what's trending:", scan links, sources
        # only in footer) + the larger trending block budget; plain broad
        # market asks keep the MARKET-TODAY structure hint.
        trending_ask = bool(_CTX_TRENDING_RE.search(question.lower()))
        hint = _TRENDING_ANSWER_HINT if trending_ask else _MARKET_ANSWER_HINT
        budget = _TRENDING_CONTEXT_MAX_CHARS if trending_ask else _CONTEXT_MAX_CHARS
        # Reserve room for the hint so it always survives truncation.
        block = block[: max(0, budget - len(hint) - 2)]
        block = f"{block}\n\n{hint}"
    except Exception as exc:
        logger.debug("question-router market-today block failed (query %.60s): %s", question, exc)
        return "", []
    sources: list[dict[str, str]] = [
        {"type": name, "query": question[:90], "title": f"{name} \u00b7 {r.source or 'live data'}"}
        for name, r in ok_results.items()
    ]
    if backend_overview:
        sources.append(
            {
                "type": "market_overview",
                "query": question[:90],
                "title": "backend /api/v1/market/overview",
            }
        )
    for n in narratives[:4]:
        if not isinstance(n, dict):
            continue
        url = str(n.get("url") or "").strip()
        title = str(n.get("title") or url or "narrative").strip()[:60]
        if url:
            sources.append({"type": "web", "title": title, "url": url})
    for n in news[:_MARKET_NEWS_LIMIT]:
        url = str(n.get("url") or "").strip()
        title = str(n.get("title") or url or "news headline").strip()[:60]
        if url:
            sources.append({"type": "news", "title": title, "url": url})
    # Cache the assembled block (5 min) so repeated market questions are fast.
    await _market_cache_set(block, sources)
    return block, sources
