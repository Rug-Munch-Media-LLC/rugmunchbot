"""Deterministic tier routing + question-type classification (regex only).

Split from the former monolithic ``_router.py``. ``_route_tier`` picks
snappy/long (mirrors the backend ``_route_task``); ``_classify_question`` +
``_classify_context_need`` decide WHAT to fetch and HOW MUCH.
"""

from __future__ import annotations

import re

from rugmunchbot.handlers.chat_ai._llm._constants import (
    _CTX_EDU_RE,
    _CTX_MARKET_BROAD_RE,
    _CTX_MARKET_RE,
    _CTX_NEED_HEAVY,
    _CTX_NEED_LIGHT,
    _CTX_NEED_NONE,
    _CTX_NEWS_RE,
    _CTX_SCAM_RE,
    _CTX_TA_RE,
    _CTX_TRENDING_RE,
    _TIER_ADDRESS_OR_HASH_RE,
    _TIER_ANALYSIS_FORENSIC_RE,
    _TIER_ANALYSIS_LENGTH_THRESHOLD,
    _TIER_ANALYSIS_QUESTION_RE,
    _TIER_ANALYSIS_TERMS,
    _TIER_DEFINITIONAL_RE,
)
from rugmunchbot.tools.guardrails import extract_addresses


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
