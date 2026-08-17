"""LLM clients — tier router + question-type router + context gathers.

Deterministic routing: ``_route_tier`` picks snappy/long (mirrors the
backend ``_route_task``); ``_classify_question`` + ``_gather_context``
retrieve 1-2 relevant sources (price / scanner / RAG KB / news_v2 + live
SearXNG fallback) so the remote LLM answers with real specifics. Every
gather is bounded and fail-open. ``_gather_web_fallback`` resolves
through the package so tests can patch ``chat_ai._llm._gather_web_fallback``
(monkeypatch contract).
"""

from __future__ import annotations

import asyncio
import html
import re
from typing import Any

from app.domains.telegram.rugmunchbot.handlers.chat_ai import _llm as _llm_mod
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._constants import (
    _CONTEXT_GATHER_TIMEOUT,
    _CONTEXT_MAX_CHARS,
    _CTX_EDU_RE,
    _CTX_MARKET_BROAD_RE,
    _CTX_MARKET_RE,
    _CTX_NEED_HEAVY,
    _CTX_NEED_LIGHT,
    _CTX_NEED_NONE,
    _CTX_NEWS_RE,
    _CTX_SCAM_RE,
    _CTX_TA_RE,
    _CTX_TOOL_SETS,
    _CTX_TRENDING_RE,
    _EDU_ANSWER_HINT,
    _EDU_RAG_THIN_CHARS,
    _EDU_RAG_THIN_HITS,
    _MARKET_ANSWER_HINT,
    _MARKET_MOVERS_DEFAULT,
    _NEWS_THIN_THRESHOLD,
    _TA_ANSWER_HINT,
    _TIER_ADDRESS_OR_HASH_RE,
    _TIER_ANALYSIS_FORENSIC_RE,
    _TIER_ANALYSIS_LENGTH_THRESHOLD,
    _TIER_ANALYSIS_QUESTION_RE,
    _TIER_ANALYSIS_TERMS,
    _TIER_DEFINITIONAL_RE,
    _TRENDING_ANSWER_HINT,
    _TRENDING_CONTEXT_MAX_CHARS,
    _TRENDING_FETCH_LIMIT,
    _WEB_FALLBACK_TIMEOUT,
    _WEB_RESULTS_MAX,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._llm._sources import _normalize_source_domain
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.guardrails import extract_addresses
from app.domains.telegram.rugmunchbot.tools.intent import _fmt_signal, build_tool_prompt_block, tool_kwargs

logger = get_logger("rmi_bot.chat_ai")


def _route_tier(message: str) -> str:
    """Route ``message`` to a remote provider tier: ``"snappy"`` (fast short
    chat answers) or ``"long"`` (longer/reasoning analysis). Mirrors the
    backend ``_route_task`` so bot and backend agree (snappy = old 4B chat
    tier / long = old 9B analysis tier)."""
    low = message.lower().strip()
    if not low:
        return "snappy"
    if len(low) > _TIER_ANALYSIS_LENGTH_THRESHOLD:
        return "long"
    if _TIER_ADDRESS_OR_HASH_RE.search(low):
        return "long"
    if _TIER_ANALYSIS_FORENSIC_RE.search(low):
        return "long"
    # MARKET-TODAY / market-analysis asks ("how's the market today", "market
    # analysis", "what to watch") go LONG: they carry the rich market context
    # block (sentiment -> movers -> narrative -> what-to-watch) and need the
    # long model's budget + REMOTE_LONG_MAX_TOKENS to write the full
    # structure — the snappy tier's lean window would truncate it. Deliberate
    # bot-side divergence from the backend _route_task (which has no
    # market-today signal): the fallback remote chain answers these on the
    # deep tier.
    if _CTX_MARKET_BROAD_RE.search(low):
        return "long"
    # TRENDING / movers LIST asks ("20 tokens trending today", "top 20
    # gainers") go LONG: the long model's output budget (REMOTE_LONG_MAX_TOKENS)
    # fits a ~20-token list with narratives + scan links, and the long tier's
    # context budget (REMOTE_LLM_CONTEXT_BUDGET) fits the bigger trending
    # block. Single-token pump/TA asks stay snappy (the TA regex overlaps
    # "pump"), and a plain "what's trending?" without a requested count stays
    # snappy too — the count is what signals a big list.
    if _CTX_TRENDING_RE.search(low) and not _CTX_TA_RE.search(low):
        want = _requested_token_count(low)
        if want is not None and want >= 10:
            return "long"
    if _TIER_DEFINITIONAL_RE.search(low):
        return "snappy"
    if _TIER_ANALYSIS_QUESTION_RE.search(low):
        return "long"
    if any(term in low for term in _TIER_ANALYSIS_TERMS):
        return "long"
    return "snappy"


# Explicit token-count ask ("20 tokens trending today", "top 10 gainers") —
# the signal that a trending/movers answer wants a BIG list (the long tier +
# a ~20-token block) instead of the compact default.
_TRENDING_COUNT_RE = re.compile(r"\b(\d{1,3})\s*(?:tokens?|coins?|gainers?|losers?|movers?)\b")


def _requested_token_count(question: str) -> int | None:
    """Explicit token count in the ask (``None`` when none is stated).

    ``"20 tokens trending today"`` -> 20; ``"top 10 gainers"`` -> 10;
    ``"what's trending"`` -> ``None``. Clamped to >= 1.
    """
    m = _TRENDING_COUNT_RE.search((question or "").lower())
    if not m:
        return None
    try:
        return max(1, int(m.group(1)))
    except ValueError:
        return None


def _classify_question(question: str) -> str:
    """Deterministic question-TYPE classifier (regex only, no LLM).

    Returns ``""`` when no type matches (greetings / generic asks — the LLM
    answers from its own knowledge). Address presence is the strongest
    signal (SPECIFIC TOKEN beats every keyword type); SCAM/SAFETY beats
    education because "what is a honeypot" is equally answered by the
    security-pattern KB (rag_lookup) that both types share.

    The TYPE alone does NOT decide how much to fetch — the cost tier
    (:func:`_classify_context_need`) gates every gather: definitional /
    conversational asks (even a scam-typed "what is a rug pull") are NONE
    (~0 gather), single-asset price asks are LIGHT, and only genuinely
    current asks (token scan, scam check, news, market-today, TA) are HEAVY.
    """
    if not question or not question.strip():
        return ""
    low = question.lower()
    if extract_addresses(question):
        return "token"
    if _CTX_SCAM_RE.search(low):
        return "scam"
    # BROAD market asks ("market today", "market analysis", "how's the
    # market", "top gainers/losers", "what to watch") beat NEWS: the user
    # wants a market-wide read (sentiment + movers + narrative), not a flat
    # news list — the rich gather below grounds it with real prices / F&G /
    # movers / narrative.
    if _CTX_MARKET_BROAD_RE.search(low):
        return "market"
    if _CTX_NEWS_RE.search(low):
        return "news"
    if _CTX_MARKET_RE.search(low):
        return "market"
    if _CTX_EDU_RE.search(low):
        return "education"
    return ""


def _classify_context_need(question: str, qtype: str) -> str:
    """COST-CLASSIFY: the context-NEED tier (none / light / heavy) for a typed
    question — the gate that decides how much ``_gather_context`` fetches.

    Returns one of ``_CTX_NEED_NONE`` / ``_CTX_NEED_LIGHT`` /
    ``_CTX_NEED_HEAVY`` (regex only, no LLM):

      * NONE  — definitional / conversational / greeting asks ("what is a rug
        pull", "how does MEV work", "gm", unknown types): the LLM answers
        from its own knowledge, ~0 gather time, NOTHING fetched (no
        news/RAG/web/tools). Current-data intents below beat this — a price
        ask "what is the price of X" still deserves a light lookup.
      * LIGHT — single-asset price ask (qtype market, not broad/TA/trending):
        ONE fast tool (price_lookup) only, no web fallback on a miss.
      * HEAVY — genuinely-current asks: token address (scanner + price),
        scam/security check, news/current events, market-today, TA/trending —
        the full bounded gather inside ``_CONTEXT_GATHER_TIMEOUT``.
    """
    if qtype == "" or not question or not question.strip():
        return _CTX_NEED_NONE
    low = question.lower()
    # Strongest signals first — current-data intents beat the definitional
    # check ("what is the price of X" is a light price ask, not a definition;
    # "how is the market" is a broad market read).
    if qtype == "token":
        return _CTX_NEED_HEAVY  # address present → scanner + price
    if qtype == "market":
        if _CTX_MARKET_BROAD_RE.search(low):
            return _CTX_NEED_HEAVY  # market-today rich context
        if _CTX_TA_RE.search(low) or _CTX_TRENDING_RE.search(low):
            return _CTX_NEED_HEAVY  # TA / trending need their tool sets
        return _CTX_NEED_LIGHT  # plain single-asset price ask
    if qtype == "news":
        return _CTX_NEED_HEAVY  # current events need current sources
    # Definitional / conversational asks answer from the model itself — NO
    # RAG KB / exploit / news / web scan (~0 gather). This is the fast path:
    # "what is a rug pull" is scam-typed but definitional → NONE.
    if _TIER_DEFINITIONAL_RE.search(low):
        return _CTX_NEED_NONE
    # Remaining typed asks (scam/education, non-definitional) are knowledge /
    # safety checks the RAG intel KB grounds — HEAVY.
    return _CTX_NEED_HEAVY


async def _gather_rag_news_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """RAG news_v2 hits via the existing ``app.rag.tools.get_news`` facade.

    Fail-open: any failure (import, timeout, empty hits) returns empty
    context so the LLM still answers from its own knowledge.
    """
    try:
        from app.rag.tools import get_news as _rag_get_news

        news = await asyncio.wait_for(_rag_get_news(question, top_k=5), timeout=_CONTEXT_GATHER_TIMEOUT)
    except Exception as exc:
        logger.debug("question-router news gather failed (query %.60s): %s", question, exc)
        return "", []
    if not isinstance(news, dict):
        return "", []
    hits = news.get("results")
    if not isinstance(hits, list) or not hits:
        return "", []
    lines = ["RAG NEWS (news_v2 — real articles; cite them by source/URL):"]
    sources: list[dict[str, str]] = []
    for h in hits[:5]:
        content = str(h.get("content") or "").strip()[:220]
        url = str(h.get("canonical_url") or "").strip()
        if not content:
            continue
        label = str(h.get("source") or url or "news article")[:60]
        lines.append(f"- {content} (source: {label})")
        if url:
            sources.append({"type": "news", "title": content[:60], "url": url})
    if len(lines) == 1:
        return "", []
    return "\n".join(lines)[:_CONTEXT_MAX_CHARS], sources


async def _gather_news_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """NEWS context: RAG news_v2 hits, PLUS live SearXNG results when the
    news_v2 corpus is thin (< ``_NEWS_THIN_THRESHOLD`` hits).

    Current-event questions ("what's happening", "latest news", "market
    today") need CURRENT, sourced intel — a thin RAG corpus cannot provide
    that, so the router falls through to the live web layer. Both blocks are
    plain text (the caller wraps them in the existing ``<untrusted_context>``
    delimiter) and their sources merge into the grounding footer.

    The RAG fetch and the SearXNG web layer run CONCURRENTLY (same pattern
    as ``_gather_edu_lore_context``): the news_v2 search can take ~5s on the
    live backend and the web layer answers in ~1s — never serialize the two
    (the old path could stack to ~8.5s inside a 5s router budget).
    """
    rag_task = asyncio.create_task(_gather_rag_news_context(question))
    web_task = asyncio.create_task(_llm_mod._gather_web_fallback(question))
    try:
        # FIRST_COMPLETED: the web layer answers fast while the news_v2
        # corpus search may take the full budget — never stall on the slow
        # RAG fetch. If RAG lands first with ≥2 hits the web fetch is
        # dropped; otherwise give the web layer the REST of the budget.
        done, pending = await asyncio.wait(
            {rag_task, web_task},
            timeout=_CONTEXT_GATHER_TIMEOUT,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if rag_task in done and web_task in pending:
            await asyncio.wait({web_task}, timeout=max(0.0, _CONTEXT_GATHER_TIMEOUT - 0.2))
    except Exception as exc:
        logger.debug("question-router news gather failed (query %.60s): %s", question, exc)
    rag_block, rag_sources = "", []
    web_block, web_sources = "", []
    try:
        if not rag_task.cancelled() and rag_task.done():
            rag_block, rag_sources = rag_task.result()
    except Exception as exc:
        logger.debug("question-router news RAG layer failed (query %.60s): %s", question, exc)
    try:
        if not web_task.cancelled() and web_task.done():
            web_block, web_sources = web_task.result()
    except Exception as exc:
        logger.debug("question-router news web layer failed (query %.60s): %s", question, exc)
    finally:
        for t in (rag_task, web_task):
            if not t.done():
                t.cancel()
        if rag_task.cancelled() or web_task.cancelled():
            await asyncio.gather(rag_task, web_task, return_exceptions=True)
    if len(rag_sources) >= _NEWS_THIN_THRESHOLD:
        return rag_block, rag_sources
    parts = [b for b in (rag_block, web_block) if b]
    if not parts:
        return "", []
    return "\n\n".join(parts)[:_CONTEXT_MAX_CHARS], rag_sources + web_sources


def _render_web_results(results: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Render SearXNG results as an HTML-escaped context block + sources.

    Web snippets/pages are UNTRUSTED data — a page could embed markup that
    tries to inject HTML into the final Telegram answer, so every field
    (snippet, title, URL) is ``html.escape``d here; the caller wraps the
    block in the ``<untrusted_context>`` delimiter and the sources flow into
    the grounding footer (``_append_source_block`` renders them as plain
    ``title — url`` text; ``_send_answer``'s single ``html.escape`` is the
    escape layer). Returns ``("", [])`` when nothing usable survived.
    """
    lines = ["LIVE WEB SEARCH (real-time web results — cite by source/URL):"]
    sources: list[dict[str, str]] = []
    seen_domains: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        title = str(r.get("title") or "").strip()
        snippet = str(r.get("snippet") or "").strip()
        if not url:
            continue
        # One page per domain — both the context block the model reads and
        # the sources footer stay clean (first result per domain wins).
        domain = _normalize_source_domain(url)
        if domain and domain in seen_domains:
            continue
        if domain:
            seen_domains.add(domain)
        text = (snippet or title).strip()
        if not text:
            continue
        label = (title or url)[:60]
        lines.append(f"- {html.escape(text[:200])} (source: {html.escape(label)} — {html.escape(url)})")
        sources.append({"type": "web", "title": text[:60], "url": url})
    if len(lines) == 1:
        return "", []
    return "\n".join(lines)[:_CONTEXT_MAX_CHARS], sources


async def _gather_web_fallback(question: str) -> tuple[str, list[dict[str, str]]]:
    """SearXNG web fallback for thin/empty router sources (fail-open).

    Uses ``tools.web.searxng_top_results`` (SearXNG-only — no bridge, no
    knowledge LLM), bounded by ``_WEB_FALLBACK_TIMEOUT`` so the router stays
    close to its ~5s snappy budget. Any failure returns empty context and
    the LLM answers from its own knowledge.
    """
    try:
        from app.domains.telegram.rugmunchbot.tools.web import searxng_top_results

        results = await asyncio.wait_for(
            searxng_top_results(question, limit=_WEB_RESULTS_MAX),
            timeout=_WEB_FALLBACK_TIMEOUT,
        )
    except Exception as exc:
        logger.debug("question-router web fallback failed (query %.60s): %s", question, exc)
        return "", []
    if not results:
        return "", []
    return _render_web_results(results)


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
    is_trending_question = qtype == "market" and not is_ta_question and bool(_CTX_TRENDING_RE.search(low))
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
        from app.domains.telegram.rugmunchbot.tools import get_registry

        results = await asyncio.wait_for(get_registry().run_many_kwargs(spec), timeout=_CONTEXT_GATHER_TIMEOUT)
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


# ── MARKET-TODAY rich context (broad market asks) ──────────────────────────


# ── TRENDING rich context (~20 tokens + narrative, "what's trending" asks) ───────────


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


async def _gather_trending_context(question: str, spec: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """TRENDING context: ~20 tokens + narrative drivers + trending hint.

    Runs the routed tools (price_lookup + trending, trending already capped
    at ``_TRENDING_FETCH_LIMIT`` by the caller) CONCURRENTLY with the
    daily-brief narrative fetch (fail-open) — same pattern as the
    market-today gather — then assembles a block that renders up to
    ``_TRENDING_FETCH_LIMIT`` tokens (fewer when the user stated a smaller
    count) with the TRENDING answer hint (open "Here's what's trending:",
    scan links, sources only in the footer). Fail-open like every gather.
    """
    from app.domains.telegram.rugmunchbot.tools import get_registry

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


async def _fetch_market_narrative() -> list[dict[str, Any]]:
    """Dominant-narrative hits (SearXNG) for the market-today block.

    Mirrors the daily brief's narrative fetch — same thematic queries and the
    same ``tools.web.searxng_top_results`` tool (no new service) — but
    bounded to the router budget: the first two queries run concurrently and
    whatever finishes inside the cap contributes; a slow query can never
    starve the market block. Fail-open → ``[]``.
    """
    try:
        from app.domains.telegram.rugmunchbot.channels.daily_market_summary import NARRATIVE_QUERIES
        from app.domains.telegram.rugmunchbot.tools.web import searxng_top_results

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
    if condition is not None and getattr(condition, "ok", False) and isinstance(condition.data, dict):
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


def _movers_from_trending(results: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split the trending feed into top gainers / top losers (24h change).

    Uses ONLY tokens with a real ``change_24h`` (never invented). Returns
    ``([], [])`` when the trending tool missed or the feed carries no
    changes.
    """
    trending_res = results.get("trending")
    if trending_res is None or not getattr(trending_res, "ok", False) or not isinstance(trending_res.data, dict):
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
    if condition is not None and getattr(condition, "ok", False) and isinstance(condition.data, dict):
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
        seeds.append(f"BTC at {price}{f' ({chg_txt} 24h)' if chg_txt else ''} — watch the level hold/break")
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
    if condition is not None and getattr(condition, "ok", False) and isinstance(condition.data, dict):
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
        _add(f"- Market condition: {verdict} (score {score_txt}, {data.get('confidence') or 'low'} confidence)")
        for reason in (data.get("reasons") or [])[:4]:
            if isinstance(reason, str) and reason.strip():
                _add(f"  * {reason.strip()}")
    else:
        # market_condition missed → fall back to market_overview's F&G only
        # (the verdict is genuinely unavailable — say so, never guess).
        overview = results.get("market_overview")
        if overview is not None and getattr(overview, "ok", False) and isinstance(overview.data, dict):
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
        _add("## DOMINANT NARRATIVE — unavailable (narrative fetch returned nothing; omit this section)")

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
    from app.domains.telegram.rugmunchbot.tools import get_registry
    from app.domains.telegram.rugmunchbot.tools.intent import tool_kwargs

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
        logger.debug("question-router market-today narrative failed (query %.60s): %s", question, exc)
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
        mover_cap = min(_requested_token_count(question) or _MARKET_MOVERS_DEFAULT, _TRENDING_FETCH_LIMIT // 2)
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


async def _gather_edu_lore_context(question: str) -> tuple[str, list[dict[str, str]]]:
    """EDUCATION / LORE context: RAG intel KB + CONCURRENT live web layer.

    Runs ``rag_lookup`` (intel KB: security_patterns / defi_exploits /
    scam_patterns) and the SearXNG web fallback CONCURRENTLY so the slow KB
    search (measured ~5s on the live backend) can never consume the whole
    router budget and starve the web layer. The web layer is MERGED when the
    KB is missing or thin (``_EDU_RAG_THIN_HITS`` / ``_EDU_RAG_THIN_CHARS``);
    a rich KB stands alone. Either way the education answer-structure hint
    (plain-language explanation → concrete example → risk/why-it-matters) is
    appended to the RETRIEVED CONTEXT block. Fail-open: any failure returns
    empty context and the LLM answers from its own knowledge.
    """
    from app.domains.telegram.rugmunchbot.tools import get_registry
    from app.domains.telegram.rugmunchbot.tools.intent import tool_kwargs

    spec = tool_kwargs(question, ["rag_lookup"])
    spec["rag_lookup"] = {"query": question}
    try:
        rag_task = asyncio.create_task(get_registry().run_many_kwargs(spec))
        web_task = asyncio.create_task(_llm_mod._gather_web_fallback(question))
    except Exception as exc:
        logger.debug("question-router edu/lore gather failed (query %.60s): %s", question, exc)
        return "", []
    try:
        # FIRST_COMPLETED: the live SearXNG layer answers in ~1s while the KB
        # search can take ~5s+ — never stall the answer on the slow KB. If the
        # KB completes first (fast backend, cache), its hits are used;
        # otherwise the web layer stands alone (fail-open: timeout caps the
        # total wait).
        done, pending = await asyncio.wait(
            {rag_task, web_task},
            timeout=_CONTEXT_GATHER_TIMEOUT,
            return_when=asyncio.FIRST_COMPLETED,
        )
        # RAG finished (or failed instantly via negative cache) but the web
        # fetch is still in flight — give the web layer the REST of the
        # router budget so a thin/absent KB never leaves the answer empty.
        if rag_task in done and web_task in pending:
            await asyncio.wait({web_task}, timeout=max(0.0, _CONTEXT_GATHER_TIMEOUT - 0.2))
    except Exception as exc:
        logger.debug("question-router edu/lore gather failed (query %.60s): %s", question, exc)
    # Collect whatever finished within the budget (fail-open on the rest).
    web_block, web_sources = "", []
    try:
        if not web_task.cancelled() and web_task.done():
            web_block, web_sources = web_task.result()
    except Exception as exc:
        logger.debug("question-router web layer failed (query %.60s): %s", question, exc)
    rag_block, rag_hits = "", []
    try:
        if not rag_task.cancelled() and rag_task.done():
            rag_results = rag_task.result() or {}
            rag_result = rag_results.get("rag_lookup")
            if rag_result is not None and getattr(rag_result, "ok", False):
                rag_data = rag_result.data if isinstance(rag_result.data, dict) else {}
                rag_hits = rag_data.get("hits") or []
                rag_block = str(rag_data.get("context") or "")
    except Exception as exc:
        logger.debug("question-router RAG layer failed (query %.60s): %s", question, exc)
    finally:
        for t in (rag_task, web_task):
            if not t.done():
                t.cancel()
        if rag_task.cancelled() or web_task.cancelled():
            await asyncio.gather(rag_task, web_task, return_exceptions=True)

    rag_thin = not rag_block or len(rag_hits) < _EDU_RAG_THIN_HITS or len(rag_block) < _EDU_RAG_THIN_CHARS
    if rag_thin and web_block:
        block = "\n\n".join(b for b in (rag_block, web_block) if b)
        sources = [
            *[{"type": "rag_lookup", "query": question[:90], "title": "rag_lookup \u00b7 intel KB"}],
            *web_sources,
        ]
        if not rag_block:
            sources = web_sources
    elif rag_block:
        block = rag_block
        sources = [{"type": "rag_lookup", "query": question[:90], "title": "rag_lookup \u00b7 intel KB"}]
    else:
        return "", []
    # Reserve room for the hint so it always survives truncation.
    block = block[: max(0, _CONTEXT_MAX_CHARS - len(_EDU_ANSWER_HINT) - 2)]
    if not block:
        return "", []
    return f"{block}\n\n{_EDU_ANSWER_HINT}", sources
