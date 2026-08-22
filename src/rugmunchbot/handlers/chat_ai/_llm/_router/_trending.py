"""TRENDING rich context (~20 tokens + narrative, "what's trending" asks)."""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.handlers.chat_ai import _llm as _llm_mod
from rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    _TRENDING_ANSWER_HINT,
    _TRENDING_CONTEXT_MAX_CHARS,
    _TRENDING_FETCH_LIMIT,
)
from rugmunchbot.handlers.chat_ai._llm._router._market import _fetch_market_narrative
from rugmunchbot.handlers.chat_ai._llm._router._routing import _requested_token_count
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.intent import _fmt_signal, build_tool_prompt_block

logger = get_logger("rmi_bot.chat_ai")


def _render_trending_tokens(
    tokens: list[Any],
    cap: int = _TRENDING_FETCH_LIMIT,
    max_chars: int | None = None,
) -> str:
    """Compact per-token lines mirroring ``tools/intent._compact_list``'s
    signal projection (the model-facing shape the intent module already
    uses) — but up to ``cap`` tokens so a "20 tokens trending" ask actually
    SEES ~20 candidates instead of the intent module's fixed 10. ``max_chars``
    (when given) bounds the output to COMPLETE lines only — never a token
    line cut mid-address (a truncated address would break the scan link).
    Pure.
    """
    if not tokens:
        return ""
    out: list[str] = []
    # LEAN projection: the 20-cap render must fit the router budget, so only
    # the fields the trending answer actually cites survive (symbol, name,
    # chain, 24h change, and the address the scan link needs). price/volume/
    # mcap per token stay out — the block would truncate before 20 tokens.
    keys = ("symbol", "name", "chain", "change_24h", "address")
    used = 0
    for item in tokens[:cap]:
        if isinstance(item, dict):
            parts = [f"{k}={_fmt_signal(k, item[k])}" for k in keys if item.get(k) is not None]
            line = "{" + ", ".join(parts) + "}"
        else:
            line = str(item)[:200]
        if max_chars is not None and used and used + len(line) > max_chars:
            break  # budget guard — stop at a complete line, never mid-token
        out.append(line)
        used += len(line) + 6  # 6 = the "\n      " join separator
    if max_chars is not None:
        return "\n      ".join(out)
    if len(tokens) > cap:
        out.append(f"... {len(tokens) - cap} more")
    return "\n      ".join(out)


def _render_trending_section(
    result: Any,
    cap: int = _TRENDING_FETCH_LIMIT,
    max_chars: int | None = None,
) -> str:
    """The trending tool section for the LLM prompt (up to ``cap`` tokens).

    Mirrors ``build_tool_prompt_block``'s section shape (source line, then
    ``tokens: {…}`` lines) so the model parses the SAME format it already
    knows — with the router's larger cap. ``max_chars`` bounds the token
    lines to complete entries. Returns ``''`` on a miss.
    """
    if result is None or not getattr(result, "ok", False) or not isinstance(result.data, dict):
        return ""
    source = getattr(result, "source", "") or "trending"
    lines = [f"- trending [source: {source}]:"]
    data = result.data
    tokens = data.get("tokens")
    if isinstance(tokens, list):
        rendered = _render_trending_tokens(tokens, cap, max_chars=max_chars)
        if rendered:
            lines.append(f"    tokens: {rendered}")
    for k, v in data.items():
        if k == "tokens":
            continue
        if isinstance(v, (dict, list)):
            lines.append(f"    {k}: {str(v)[:500]}")
        else:
            lines.append(f"    {k}: {_fmt_signal(k, v)[:500]}")
    return "\n".join(lines)


def _build_trending_context_block(
    ok_results: dict[str, Any],
    narratives: list[dict[str, Any]],
    cap: int = _TRENDING_FETCH_LIMIT,
    max_chars: int | None = None,
) -> str:
    """Trending-typed context block: up to ``cap`` tokens + narrative hits.

    Renders the trending tool itself with the router's larger cap (the shared
    intent renderer caps token lists at 10) and the remaining tools via the
    shared ``build_tool_prompt_block``, then appends the daily-brief narrative
    hits (same shape as the market-today block) so the model can tie drivers
    to movers. Pure function, never raises.
    """
    other = {name: r for name, r in ok_results.items() if name != "trending"}
    parts: list[str] = []
    if other:
        parts.append(build_tool_prompt_block(other))
    trend = _render_trending_section(ok_results.get("trending"), cap, max_chars=max_chars)
    if trend:
        parts.append(trend)
    if narratives:
        lines = ["## DOMINANT NARRATIVE (daily-brief narrative fetch)"]
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
            lines.append(line)
        parts.append("\n".join(lines))
    if not parts:
        return "LIVE MARKET DATA (real, fetched just now):\n- no live tool data"
    return "\n\n".join(parts)


async def _gather_trending_context(
    question: str, spec: dict[str, Any]
) -> tuple[str, list[dict[str, str]]]:
    """TRENDING context: ~20 tokens + narrative drivers + trending hint.

    Runs the routed tools (price_lookup + trending, trending already capped
    at ``_TRENDING_FETCH_LIMIT`` by the caller) CONCURRENTLY with the
    daily-brief narrative fetch (fail-open) — same pattern as the
    market-today gather — then assembles a block that renders up to
    ``_TRENDING_FETCH_LIMIT`` tokens (fewer when the user stated a smaller
    count) with the TRENDING answer hint (open "Here's what's trending:",
    scan links, sources only in the footer). Fail-open like every gather.
    """
    from rugmunchbot.tools import get_registry

    render_cap = min(_requested_token_count(question) or 10, _TRENDING_FETCH_LIMIT)
    # Budget the token lines to COMPLETE entries: reserve the hint + a slice
    # for the narrative/header so no address is ever cut mid-line (a cut
    # address would break the scan link the hint tells the model to emit).
    block_budget = max(0, _TRENDING_CONTEXT_MAX_CHARS - len(_TRENDING_ANSWER_HINT) - 2)
    token_budget = max(0, block_budget - 700)
    try:
        tools_task = asyncio.create_task(get_registry().run_many_kwargs(spec))
        narrative_task = asyncio.create_task(_fetch_market_narrative())
    except Exception as exc:
        logger.debug("question-router trending gather failed (query %.60s): %s", question, exc)
        return "", []
    try:
        await asyncio.wait(
            {tools_task, narrative_task},
            timeout=_CONTEXT_GATHER_TIMEOUT,
            return_when=asyncio.ALL_COMPLETED,
        )
    except Exception as exc:
        logger.debug("question-router trending gather failed (query %.60s): %s", question, exc)
    tools: dict[str, Any] = {}
    narratives: list[dict[str, Any]] = []
    try:
        if not tools_task.cancelled() and tools_task.done():
            tools = tools_task.result() or {}
    except Exception as exc:
        logger.debug("question-router trending tools failed (query %.60s): %s", question, exc)
    try:
        if not narrative_task.cancelled() and narrative_task.done():
            narratives = narrative_task.result() or []
    except Exception as exc:
        logger.debug("question-router trending narrative failed (query %.60s): %s", question, exc)
    finally:
        for t in (tools_task, narrative_task):
            if not t.done():
                t.cancel()
        if tools_task.cancelled() or narrative_task.cancelled():
            await asyncio.gather(tools_task, narrative_task, return_exceptions=True)

    ok_results = {name: r for name, r in tools.items() if getattr(r, "ok", False)}
    if not ok_results:
        return await _llm_mod._gather_web_fallback(question)
    try:
        block = _build_trending_context_block(
            ok_results,
            narratives,
            cap=render_cap,
            max_chars=token_budget,
        )
        # Reserve room for the hint so it always survives truncation — the
        # trending block gets its own (larger) budget for ~20 tokens.
        block = block[: max(0, _TRENDING_CONTEXT_MAX_CHARS - len(_TRENDING_ANSWER_HINT) - 2)]
        block = f"{block}\n\n{_TRENDING_ANSWER_HINT}"
    except Exception as exc:
        logger.debug("question-router trending block failed (query %.60s): %s", question, exc)
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
