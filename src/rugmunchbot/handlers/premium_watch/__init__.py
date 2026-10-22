"""Paid-tier watch UX: /watch, /unwatch, /watchlist, /export.

The commands read and write the premium watch store through the backend, so
the bot holds no second list. Quotas and the refusal copy live in
:mod:`_limits`; the transport lives in :mod:`_api`.
"""

from __future__ import annotations

from rugmunchbot.handlers.premium_watch._commands import (
    cmd_export,
    cmd_unwatch,
    cmd_watch,
    cmd_watchlist,
)
from rugmunchbot.handlers.premium_watch._limits import (
    FREE_WATCHES,
    PAID_WATCHES,
    limit_refusal,
    watch_limit,
)

__all__ = [
    "FREE_WATCHES",
    "PAID_WATCHES",
    "cmd_export",
    "cmd_unwatch",
    "cmd_watch",
    "cmd_watchlist",
    "limit_refusal",
    "watch_limit",
]
