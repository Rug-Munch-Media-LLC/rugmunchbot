"""Intent routing — decide which tools to run for a user question (package).

Modularized from the former single-module ``tools/intent.py`` (2026-08-21).
This ``__init__`` is a pure re-export shim: every public name and signature
is unchanged, and callers keep importing ``tools.intent`` exactly as before.

Deterministic, cheap, and fast (no LLM round-trip for routing): keyword +
pattern matching on the user's question. The tools themselves always return
REAL data; the router only decides WHICH tools are relevant.

Layout:
  * ``_vocab.py``  — keyword tables, chain aliases, concept->tool mapping
  * ``_detect.py`` — negations, exclusions, comparisons, chains, risk rank
  * ``_route.py``  — route_tools + tool_kwargs (pure, deterministic)
  * ``_render.py`` — signal formatting + compact prompt blocks
"""

from __future__ import annotations

from rugmunchbot.tools.intent._detect import (
    _concept_for_word as _concept_for_word,
    _excluded_tools as _excluded_tools,
    compare_pairs as compare_pairs,
    detect_comparison as detect_comparison,
    detect_exclusions as detect_exclusions,
    detect_negations as detect_negations,
    detect_risk_rank as detect_risk_rank,
    extract_chain as extract_chain,
)
from rugmunchbot.tools.intent._render import (
    _PCT_FIELDS as _PCT_FIELDS,
    _USD_FIELDS as _USD_FIELDS,
    _compact_list as _compact_list,
    _fmt_scaled as _fmt_scaled,
    _fmt_signal as _fmt_signal,
    _fmt_usd as _fmt_usd,
    build_tool_prompt_block as build_tool_prompt_block,
)
from rugmunchbot.tools.intent._route import (
    route_tools as route_tools,
    tool_kwargs as tool_kwargs,
)
from rugmunchbot.tools.intent._vocab import (
    _CHAIN_ALIASES as _CHAIN_ALIASES,
    _CHAIN_RE as _CHAIN_RE,
    _CHART_WORDS as _CHART_WORDS,
    _COMPARE_RE as _COMPARE_RE,
    _CONCEPT_WORD_TOOLS as _CONCEPT_WORD_TOOLS,
    _EXCLUSION_MARKER_RE as _EXCLUSION_MARKER_RE,
    _MARKET_CONDITION_WORDS as _MARKET_CONDITION_WORDS,
    _MARKET_OVERVIEW_WORDS as _MARKET_OVERVIEW_WORDS,
    _NEGATION_WORDS as _NEGATION_WORDS,
    _NEWS_WORDS as _NEWS_WORDS,
    _PATTERN_WORDS as _PATTERN_WORDS,
    _PICKS_WORDS as _PICKS_WORDS,
    _PREDICT_TA_WORDS as _PREDICT_TA_WORDS,
    _PREDICTION_WORDS as _PREDICTION_WORDS,
    _PRICE_WORDS as _PRICE_WORDS,
    _RISK_RANK_WORDS as _RISK_RANK_WORDS,
    _SECURITY_WORDS as _SECURITY_WORDS,
    _SMART_MONEY_WORDS as _SMART_MONEY_WORDS,
    _TA_WORDS as _TA_WORDS,
    _TRENDING_WORDS as _TRENDING_WORDS,
    _WALLET_WORDS as _WALLET_WORDS,
)

__all__ = [
    "build_tool_prompt_block",
    "compare_pairs",
    "detect_comparison",
    "detect_exclusions",
    "detect_negations",
    "detect_risk_rank",
    "extract_chain",
    "route_tools",
    "tool_kwargs",
]
