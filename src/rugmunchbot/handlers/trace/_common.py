#!/usr/bin/env python3
"""
RugMunchBot -- /trace command: Cross-chain fund flow tracing.

Hunter tier and above. Traces fund flows across chains, identifies
funding sources, exchange interactions, and connected wallets.
"""

from __future__ import annotations

from rugmunchbot.support.logging import get_logger

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



def _label_exchange(name: str) -> str:
    """Return emoji-labeled exchange name."""
    key = name.lower().replace(".", "").replace(" ", "_")
    for k, v in EXCHANGE_LABELS.items():
        if k in key or key in k:
            return v
    return f"🏦 {name}"


