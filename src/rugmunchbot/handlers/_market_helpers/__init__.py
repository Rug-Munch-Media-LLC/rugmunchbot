"""Pure formatting / rendering helpers for the market command handlers (Batch 13).

Extracted from ``handlers/market.py``: trending-chain aliases + badge maps,
time-ago / age / liquidity / risk rendering, trending merge/sort/window
logic, the HTML trending list renderer, and the trending keyboard builder.
No network I/O, no database access — only telegram keyboard primitives and
``config`` / ``formatters`` formatting helpers.

Split into cohesive submodules (2026-10-23); this ``__init__`` is a pure
re-export shim so every existing import path keeps working:

  * ``_badges.py`` — chain aliases, badge tables, compact value rendering
  * ``_tokens.py`` — address/age primitives, windows, ranking bonuses
  * ``_names.py``  — display-safe token names/symbols
  * ``_rank.py``   — merge, dedup, ranking, window footnotes
  * ``_render.py`` — HTML trending list + trending keyboard
"""

from __future__ import annotations

from rugmunchbot.handlers._market_helpers._badges import (
    CHAIN_BADGES as CHAIN_BADGES,
    LIQ_TIERS as LIQ_TIERS,
    RUGCHARTS_CHAIN_ALIASES as RUGCHARTS_CHAIN_ALIASES,
    TRENDING_CHAINS as TRENDING_CHAINS,
    _chain_badge as _chain_badge,
    _fmt_price as _fmt_price,
    _liq_badge as _liq_badge,
    _resolve_chain as _resolve_chain,
    _risk_emoji as _risk_emoji,
    _time_ago as _time_ago,
)
from rugmunchbot.handlers._market_helpers._names import (
    _clean_token_name as _clean_token_name,
    _trending_display as _trending_display,
)
from rugmunchbot.handlers._market_helpers._rank import (
    _merge_trending_sources as _merge_trending_sources,
    _sort_trending as _sort_trending,
    _window_note as _window_note,
)
from rugmunchbot.handlers._market_helpers._render import (
    _build_trending_kb as _build_trending_kb,
    _render_trending_text as _render_trending_text,
)
from rugmunchbot.handlers._market_helpers._tokens import (
    _WINDOW_FIELD as _WINDOW_FIELD,
    AGE_BADGES as AGE_BADGES,
    TRENDING_WINDOWS as TRENDING_WINDOWS,
    _age_badge as _age_badge,
    _freshness_bonus as _freshness_bonus,
    _mover_bonus as _mover_bonus,
    _normalize_address as _normalize_address,
    _token_age_hours as _token_age_hours,
    _window_change as _window_change,
)

__all__ = [
    "AGE_BADGES",
    "CHAIN_BADGES",
    "LIQ_TIERS",
    "RUGCHARTS_CHAIN_ALIASES",
    "TRENDING_CHAINS",
    "TRENDING_WINDOWS",
    "_age_badge",
    "_build_trending_kb",
    "_chain_badge",
    "_clean_token_name",
    "_fmt_price",
    "_freshness_bonus",
    "_liq_badge",
    "_merge_trending_sources",
    "_mover_bonus",
    "_normalize_address",
    "_render_trending_text",
    "_resolve_chain",
    "_risk_emoji",
    "_sort_trending",
    "_time_ago",
    "_token_age_hours",
    "_trending_display",
    "_window_change",
    "_window_note",
]
