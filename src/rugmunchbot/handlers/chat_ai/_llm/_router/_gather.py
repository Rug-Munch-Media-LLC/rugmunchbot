"""Thin question router — cost-gated context gathering entry point."""

from __future__ import annotations

import asyncio

from rugmunchbot.handlers.chat_ai import _llm as _llm_mod
from rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    _CONTEXT_MAX_CHARS,
    _CTX_MARKET_BROAD_RE,
    _CTX_NEED_LIGHT,
    _CTX_NEED_NONE,
    _CTX_TA_RE,
    _CTX_TOOL_SETS,
    _CTX_TRENDING_RE,
    _TA_ANSWER_HINT,
    _TRENDING_FETCH_LIMIT,
)
from rugmunchbot.handlers.chat_ai._llm._router._edu import _gather_edu_lore_context
from rugmunchbot.handlers.chat_ai._llm._router._market import _gather_market_today_context
from rugmunchbot.handlers.chat_ai._llm._router._news import _gather_news_context
from rugmunchbot.handlers.chat_ai._llm._router._routing import (
    _classify_context_need,
    _classify_question,
)
from rugmunchbot.handlers.chat_ai._llm._router._trending import _gather_trending_context
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.guardrails import extract_addresses
from rugmunchbot.tools.intent import build_tool_prompt_block, tool_kwargs

logger = get_logger("rmi_bot.chat_ai")


async def _gather_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """Thin question router: retrieve 1-2 relevant sources for ``question``.

    COST-AWARE: the context-NEED tier (:func:`_classify_context_need`) gates
    EVERY gather. NONE (definitional / conversational / greeting / unknown)
    returns immediately with NOTHING fetched — ~0 gather time — so "what is
    a rug pull" answers fast from the model instead of scanning news/RAG/
    exploits. LIGHT (single-asset price ask) runs price_lookup ONLY, no web
    fallback. HEAVY runs the full bounded gather below (~5s, fail-open).

    Returns ``(context_block, sources)`` — both empty when the tier is NONE,
    no source answered, or the 5s budget expired (fail-open). The caller
    passes these into the remote LLM's existing ``sources`` param and the
    separately-budgeted RETRIEVED CONTEXT message, so the model answers with
    real data instead of generic knowledge.

    Need tier → what is fetched:
      * NONE   → nothing (fast path: definitional / greetings / unknown)
      * LIGHT  → price_lookup only (single-asset price ask)
      * HEAVY  → per type below, bounded by _CONTEXT_GATHER_TIMEOUT

    Type → source map (implemented, all HEAVY except plain price):
      * MARKET/TA      → price_lookup (+ technical_analysis / trending);
                         live web results when the tool data is thin
      * SPECIFIC TOKEN → scanner_intel + price_lookup (address present)
      * SCAM/SAFETY    → rag_lookup security-pattern KB (+ scanner_intel when address)
      * EDUCATION/LORE → rag_lookup intel KB (security_patterns / defi_exploits / scam_patterns);
                         live web results when the KB is thin
      * NEWS           → RAG news_v2 via app.rag.tools.get_news, PLUS live
                         SearXNG results when the news_v2 corpus is thin
      Web results are HTML-escaped, untrusted-wrapped (``<untrusted_context>``
      by the caller) and cited as sources (``type: "web"``).
    """
    qtype = _classify_question(question)
    need = _classify_context_need(question, qtype)
    # Cost tier gate: NONE (definitional / conversational / greeting /
    # unknown) fetches NOTHING — the LLM answers from its own knowledge in
    # ~0 router time (no news/RAG/web/tool fetches).
    if need == _CTX_NEED_NONE or not qtype:
        return "", []
    if qtype == "news":
        return await _gather_news_context(question)
    # EDUCATION / LORE (qtype "education" or address-less "scam") are pure
    # knowledge asks — they answer from the RAG intel KB (rag_lookup) with a
    # CONCURRENT SearXNG fallback (the KB search can take ~5s on the live
    # backend — it must never starve the web layer), plus the education
    # answer-structure hint (see ``_gather_edu_lore_context``).
    if qtype in ("education", "scam"):
        return await _gather_edu_lore_context(question)

    names: list[str] = list(_CTX_TOOL_SETS.get(qtype, []))
    low = question.lower()
    # TA-typed question → run technical_analysis alongside price_lookup and
    # append the TA answer-format hint to the retrieved context (see
    # ``_TA_ANSWER_HINT``). TRENDING questions get the trending tool instead.
    is_ta_question = qtype == "market" and bool(_CTX_TA_RE.search(low))
    is_trending_question = (
        qtype == "market" and not is_ta_question and bool(_CTX_TRENDING_RE.search(low))
    )
    # BROAD market-today / market-analysis asks get the RICH market context
    # (majors + sentiment/verdict + gainers & losers + narrative + what to
    # watch) via a dedicated gather — single-token price/TA asks keep the
    # lean price_lookup path below.
    if qtype == "market" and _CTX_MARKET_BROAD_RE.search(low):
        return await _gather_market_today_context(question)
    if qtype == "market":
        if is_ta_question and "technical_analysis" not in names:
            names.append("technical_analysis")
        elif _CTX_TRENDING_RE.search(low) and "trending" not in names:
            names.append("trending")
    light = need == _CTX_NEED_LIGHT
    if light:
        # LIGHT: single-asset price ask → price_lookup ONLY — no TA/trending
        # extras (the regexes above already excluded them) and no web
        # fallback on a miss: this is the fast price path.
        names = [n for n in names if n == "price_lookup"]
    # scanner_intel needs an address — drop it when the question names none
    # (avoids a pointless "address required" miss).
    if not extract_addresses(question):
        names = [n for n in names if n != "scanner_intel"]
    if not names:
        return "", []

    spec = tool_kwargs(question, names)
    if "rag_lookup" in names:
        spec["rag_lookup"] = {"query": question}
    # TRENDING asks: fetch ~20 candidates (the intent default is 10) and run
    # the daily-brief narrative fetch CONCURRENTLY so the answer can list up
    # to 20 tokens with one-line drivers — assembled with the TRENDING hint
    # (open "Here's what's trending:", scan links, sources only in footer).
    if is_trending_question:
        if spec.get("trending"):
            spec["trending"]["limit"] = _TRENDING_FETCH_LIMIT
        return await _gather_trending_context(question, spec)
    try:
        from rugmunchbot.tools import get_registry

        results = await asyncio.wait_for(
            get_registry().run_many_kwargs(spec), timeout=_CONTEXT_GATHER_TIMEOUT
        )
    except Exception as exc:
        logger.debug("question-router context gather failed (query %.60s): %s", question, exc)
        return "", []

    ok_results = {name: r for name, r in results.items() if r.ok}
    if not ok_results:
        # LIGHT price asks never spawn the web layer — empty context degrades
        # to the model's own knowledge ("price unavailable"); light means
        # ONE fast tool, nothing more.
        if light:
            return "", []
        # MARKET/TA or LORE question with thin/empty tool data → pull CURRENT
        # web results so the LLM still gets sourced context instead of
        # answering from training memory (fail-open: "" when SearXNG is
        # down too).
        return await _llm_mod._gather_web_fallback(question)
    try:
        block = build_tool_prompt_block(ok_results)
        if is_ta_question:
            # Reserve room for the hint so it always survives truncation.
            block = block[: max(0, _CONTEXT_MAX_CHARS - len(_TA_ANSWER_HINT) - 2)]
            block = f"{block}\n\n{_TA_ANSWER_HINT}"
        else:
            block = block[:_CONTEXT_MAX_CHARS]
    except Exception as exc:
        logger.debug("question-router prompt block failed (query %.60s): %s", question, exc)
        return "", []
    sources = [
        {"type": name, "query": question[:90], "title": f"{name} \u00b7 {r.source or 'live data'}"}
        for name, r in ok_results.items()
    ]
    return block, sources
