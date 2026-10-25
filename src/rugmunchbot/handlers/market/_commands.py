"""market — command handlers facade (split into per-command modules, 2026-10-25).

The pre-split public surface stays importable from this module and from
``rugmunchbot.handlers.market``; one module per command, no shared state.
"""

from __future__ import annotations

from ._cmd_launches import cmd_launches
from ._cmd_market import cmd_market
from ._cmd_movers import cmd_movers
from ._cmd_news import cmd_news
from ._cmd_rugcheck import cmd_rugcheck
from ._cmd_trending import cmd_trending
from ._cmd_whales import cmd_whales
from ._constants import MARKET_UNAVAILABLE
from ._register import register_market

__all__ = [
    "MARKET_UNAVAILABLE",
    "cmd_launches",
    "cmd_market",
    "cmd_movers",
    "cmd_news",
    "cmd_rugcheck",
    "cmd_trending",
    "cmd_whales",
    "register_market",
]
