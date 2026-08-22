"""MARKET-TODAY rich context (broad market asks) + shared narrative fetch."""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.handlers.chat_ai import _llm as _llm_mod
from rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    _CONTEXT_MAX_CHARS,
    _CTX_TA_RE,
    _CTX_TRENDING_RE,
    _MARKET_ANSWER_HINT,
    _MARKET_MOVERS_DEFAULT,
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
) -> str:
    """Assemble the rich market-today context block from real tool payloads.

    Pure function (no I/O): merges the market_condition verdict + Fear&Greed,
    market_overview majors (BTC/ETH/SOL price + 24h) + global mcap/dominance,
    trending tokens (top gainers AND losers with % and a narrative-backed
    driver when supported), the daily-brief narrative hits and a
    what-to-watch seed. ``mover_cap`` is the per-side cap (default 3); a
    count-bearing ask raises it so gainers + losers surface ~20 tokens total.
    Every line carries ONLY numbers present in the payloads — a missing
    signal renders as ``unavailable``, never invented. ``results`` is the
    ``{name: ToolResult}`` dict from the registry; ``narratives`` the
    ``{title, url, snippet, query}`` hits from the daily-brief narrative
    fetch.
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
    daily-brief narrative fetch (SearXNG, fail-open) and assembles the
    structured block — majors → sentiment/macro → gainers & losers (with
    narrative-backed drivers) → narrative → what-to-watch seed — with the
    MARKET-TODAY answer-structure hint appended (same pattern as the TA/EDU
    hints). Fail-open: any tool or narrative that misses simply doesn't
    contribute; when NO tool answered, the web fallback stands in. Never
    raises.
    """
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
    except Exception as exc:
        logger.debug("question-router market-today gather failed (query %.60s): %s", question, exc)
        return "", []
    try:
        # Run tools + narrative CONCURRENTLY to the full router budget; both
        # get the whole window (no FIRST_COMPLETED cancellation bug).
        await asyncio.wait(
            {tools_task, narrative_task},
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
    finally:
        for t in (tools_task, narrative_task):
            if not t.done():
                t.cancel()
        if tools_task.cancelled() or narrative_task.cancelled():
            await asyncio.gather(tools_task, narrative_task, return_exceptions=True)

    ok_results = {name: r for name, r in tools.items() if getattr(r, "ok", False)}
    if not ok_results:
        # All market tools missed → CURRENT web results so the answer still
        # gets sourced context (fail-open: "" when SearXNG is down too).
        return await _llm_mod._gather_web_fallback(question)
    try:
        # A count-bearing ask ("top 20 gainers") raises the per-side mover
        # cap toward _TRENDING_FETCH_LIMIT // 2 (~20 tokens total).
        mover_cap = min(
            _requested_token_count(question) or _MARKET_MOVERS_DEFAULT, _TRENDING_FETCH_LIMIT // 2
        )
        block = _build_market_today_block(ok_results, narratives, mover_cap=mover_cap)
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
    for n in narratives[:4]:
        if not isinstance(n, dict):
            continue
        url = str(n.get("url") or "").strip()
        title = str(n.get("title") or url or "narrative").strip()[:60]
        if url:
            sources.append({"type": "web", "title": title, "url": url})
    return block, sources
