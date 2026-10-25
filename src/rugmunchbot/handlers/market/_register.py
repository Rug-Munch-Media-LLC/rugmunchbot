"""market — handler registration (order is part of the bot contract)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.ext import CommandHandler

from ._cmd_launches import cmd_launches
from ._cmd_market import cmd_market
from ._cmd_movers import cmd_movers
from ._cmd_trending import cmd_trending

if TYPE_CHECKING:
    from telegram.ext import Application


def register_market(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register market-awareness commands on the bot application.

    Wires /market, /trending, /movers, /launches and /whales in one call.
    The Lead wires this from bot.py; replace the old per-command registrations
    (e.g. ``_reg(app, "trending", cmd_trending)``) with this call so commands
    are not double-registered.
    """
    app.add_handler(CommandHandler("market", cmd_market))
    app.add_handler(CommandHandler("trending", cmd_trending))
    app.add_handler(CommandHandler("movers", cmd_movers))
    app.add_handler(CommandHandler("launches", cmd_launches))
