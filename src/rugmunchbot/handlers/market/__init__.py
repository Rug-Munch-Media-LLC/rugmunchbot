"""RugMunchBot — Market data command handlers.

Facade package (split from handlers/market.py, 2026-09-18): the complete
original public surface stays importable from `rugmunchbot.handlers.market`.
"""

from __future__ import annotations

from rugmunchbot.handlers._market_helpers import (
    _time_ago,
)
from rugmunchbot.handlers._trending import (
    _fetch_cg_trending_news,
    _fetch_fused_trending,
)

from ._commands import (
    MARKET_UNAVAILABLE,
    cmd_launches,
    cmd_market,
    cmd_movers,
    cmd_news,
    cmd_rugcheck,
    cmd_trending,
    cmd_whales,
    register_market,
)
from ._data import (
    _TRENDING_KEY_PREFIX,
    _TRENDING_TTL,
    _cache_get_trending,
    _cache_set_trending,
    _fetch_market_intel,
    _redis_client,
)
from ._render import (
    _change_emoji,
    _fmt_usd,
    _mover_link,
    _render_movers_lines,
    render_trending,
)

__all__ = [
    "MARKET_UNAVAILABLE",
    "_TRENDING_KEY_PREFIX",
    "_TRENDING_TTL",
    "_cache_get_trending",
    "_cache_set_trending",
    "_change_emoji",
    "_fetch_cg_trending_news",
    "_fetch_fused_trending",
    "_fetch_market_intel",
    "_fmt_usd",
    "_mover_link",
    "_redis_client",
    "_render_movers_lines",
    "_time_ago",
    "cmd_launches",
    "cmd_market",
    "cmd_movers",
    "cmd_news",
    "cmd_rugcheck",
    "cmd_trending",
    "cmd_whales",
    "register_market",
    "render_trending",
]
