#!/usr/bin/env python3
"""
RugMunchBot -- /trace command: Cross-chain fund flow tracing.

Hunter tier and above. Traces fund flows across chains, identifies
funding sources, exchange interactions, and connected wallets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.ext import CommandHandler

from rugmunchbot.support.logging import get_logger

from ._cmd_dumps import cmd_dumps
from ._cmd_flow import cmd_flow

if TYPE_CHECKING:
    from telegram.ext import Application

logger = get_logger("rmi_bot.trace")

EXCHANGE_LABELS = {
    "binance": "🏦 Binance",
    "coinbase": "🏦 Coinbase",
    "kraken": "🏦 Kraken",
    "bybit": "🏦 Bybit",
    "okx": "🏦 OKX",
    "gate": "🏦 Gate.io",
    "kucoin": "🏦 KuCoin",
    "mexc": "🏦 MEXC",
    "bitget": "🏦 Bitget",
    "huobi": "🏦 HTX",
    "unknown_exchange": "🏦 Exchange",
}





def register_trace_commands(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register /dumps and /flow on the bot application.

    /trace is registered separately by bot.py (via ``_reg``); this helper
    wires the two transaction-tracing commands ported from the legacy
    rugmunchbot repo. Call from bot.py after the other command registrations:

        register_trace_commands(app)
    """
    app.add_handler(CommandHandler("dumps", cmd_dumps))
    app.add_handler(CommandHandler("flow", cmd_flow))
