"""Deterministic intent detection — negations, exclusions, chains."""

from __future__ import annotations

import re

from rugmunchbot.tools.classify import (
    SYMBOL_RE,
)
from rugmunchbot.tools.intent._vocab import (
    _CHAIN_ALIASES,
    _CHAIN_RE,
    _COMPARE_RE,
    _CONCEPT_WORD_TOOLS,
    _EXCLUSION_MARKER_RE,
    _NEGATION_WORDS,
    _RISK_RANK_WORDS,
)


def _concept_for_word(word: str) -> str:
    """Map a single token to its intent concept ('' when none)."""
    w = word.strip().lower()
    if w in {"trending", "trend", "hot", "pumping", "movers", "gainers"}:
        return "trending"
    if w in {"market", "markets"}:
        return "market"
    if w in {"sentiment", "bullish", "bearish", "condition", "risk-on", "risk-off"}:
        return "sentiment"
    if w in {"price", "prices", "mcap", "worth", "value"}:
        return "price"
    if w in {"chart", "charts"}:
        return "chart"
    if w in {"pattern", "patterns"}:
        return "pattern"
    if w in {"ta", "technical", "indicators", "rsi", "macd", "ema"}:
        return "technical"
    if w in {"honeypot", "rug", "risk", "risky", "safe", "security", "scam", "audit", "scan"}:
        return "security"
    if w in {"wallet", "holdings", "portfolio", "pnl", "balance"}:
        return "wallet"
    if w in {"news", "headlines", "context"}:
        return "news"
    if w in {"prediction", "odds", "polymarket", "kalshi"}:
        return "prediction"
    return ""


def detect_negations(text: str) -> set[str]:
    """Canonical intent concepts the question NEGATES (metadata, not routing).

    E.g. ``"what's NOT trending?"`` -> ``{"trending"}``;
    ``"is $BONK not a honeypot?"`` -> ``{"security"}``.

    Pure negation does NOT suppress a tool — the tool still runs so the
    answer can be grounded in real data (the LLM handles the negative
    construction). Request-level EXCLUSIONS ("don't show me X") are handled
    separately by :func:`detect_exclusions` and DO suppress routing.
    """
    tokens = re.findall(r"[a-z0-9']+", text.lower())
    concepts: set[str] = set()
    for i, tok in enumerate(tokens):
        if tok not in _NEGATION_WORDS:
            continue
        for j in range(i + 1, min(i + 3, len(tokens))):
            concept = _concept_for_word(tokens[j])
            if concept:
                concepts.add(concept)
    return concepts


def detect_exclusions(text: str) -> set[str]:
    """Concepts the user explicitly asked to EXCLUDE from the answer.

    E.g. ``"don't show me trending"`` -> ``{"trending"}``;
    ``"without the market stuff"`` -> ``{"market"}``;
    ``"except the news"`` -> ``{"news"}``.

    These concepts' tools are NOT routed (the user said not to show them) —
    see :func:`route_tools`. Conservative: only the FIRST concept after an
    exclusion marker is treated as excluded, so a trailing positive ask
    ("don't show me trending, give me prices") is never over-suppressed.
    """
    out: set[str] = set()
    for m in _EXCLUSION_MARKER_RE.finditer(text):
        tail = text[m.end() : m.end() + 80].lower()
        for word in re.findall(r"[a-z0-9']+", tail):
            concept = _concept_for_word(word)
            if concept:
                out.add(concept)
                break
    return out


def _excluded_tools(text: str) -> set[str]:
    """Tool names suppressed by explicit user exclusions."""
    excluded: set[str] = set()
    for concept in detect_exclusions(text):
        excluded.update(_CONCEPT_WORD_TOOLS.get(concept, ()))
    return excluded


def detect_comparison(text: str) -> bool:
    """True when the question explicitly asks to compare two tokens."""
    return bool(_COMPARE_RE.search(text))


def compare_pairs(text: str) -> list[tuple[str, str]]:
    """Extract ``($LEFT, $RIGHT)`` symbol pairs around a comparison connector.

    ``"compare $BONK vs $WIF"`` -> ``[("BONK", "WIF")]``. Only real
    ``$SYMBOL`` mentions are used — never synthesized from prose.
    """
    syms = [(m.start(), m.group(1).upper()) for m in SYMBOL_RE.finditer(text)]
    out: list[tuple[str, str]] = []
    for m in _COMPARE_RE.finditer(text):
        before = [s for pos, s in syms if pos < m.start()]
        after = [s for pos, s in syms if pos > m.end()]
        if before and after:
            out.append((before[-1], after[0]))
    return out


def extract_chain(text: str) -> str:
    """First canonical chain name mentioned in the question ('' if none).

    Returns the canonical DexScreener-style chain id ("solana", "ethereum",
    "base", "bsc", ...) or '' when no chain alias is present. Pure regex —
    never infers a chain from an address prefix.
    """
    m = _CHAIN_RE.search(text)
    if not m:
        return ""
    return _CHAIN_ALIASES[m.group(1).lower()]


def detect_risk_rank(text: str) -> bool:
    """True when the question asks for RISK-FIRST picks (riskiest/sketchiest).

    Maps "riskiest/sketchiest/red flags/most likely to rug" onto a
    risk-ranked trending answer (trend_by_chain rank="risk"). Pure regex —
    the -iest/-y forms are matched deliberately, not just bare "risk".
    """
    return bool(_RISK_RANK_WORDS.search(text))
