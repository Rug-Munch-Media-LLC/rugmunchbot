"""chart tool constants — networks, chain aliases, timeframes, spark chars.

Extracted verbatim from the former single-module ``tools/charting.py``.
"""

from __future__ import annotations

from typing import Any

# GeckoTerminal network ids — mirrors ``app/routers/rugcharts_helpers.GECKO_NETWORKS``
# and ``tools/ta.py`` (keep in sync).
_GECKO_NETWORKS: dict[str, str] = {
    "solana": "solana",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "tron": "tron",
    "polygon": "polygon_pos",
    "avalanche": "avax",
    "optimism": "optimism",
}

# Chain aliases -> canonical chain id — subset of ``tools/trend_algo._CHAIN_ALIASES``
# and ``tools/ta.py._CHAIN_ALIASES`` (kept local; keep in sync).
_CHAIN_ALIASES: dict[str, str] = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "0x1": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "binance-smart-chain": "bsc",
    "arb": "arbitrum",
    "arbitrum": "arbitrum",
    "base": "base",
    "polygon": "polygon",
    "matic": "polygon",
    "polygon-pos": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avax": "avalanche",
    "avalanche": "avalanche",
    "tron": "tron",
    "trx": "tron",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "zksync": "zksync",
    "scroll": "scroll",
    "mantle": "mantle",
    "sei": "sei",
    "sui": "sui",
    "aptos": "aptos",
    "injective": "injective",
    "berachain": "berachain",
    "bera": "berachain",
    "hyperliquid": "hyperliquid",
    "mode": "mode",
    "fantom": "fantom",
    "ftm": "fantom",
}

_GECKO_OHLCV_URL = (
    "https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pair}/ohlcv/{unit}"
)

# user timeframe -> (backend candle unit, gecko endpoint suffix, candles shown)
_TIMEFRAMES: dict[str, tuple[str, str, int]] = {
    "24h": ("1h", "hour", 24),
    "7d": ("1d", "day", 7),
}

# (candles, source_label) — the shape both candle primaries return so
# ``resolve_with_fallback`` enforces one contract regardless of source.
_Candle = dict[str, Any]
_CandleResult = tuple[list[_Candle], str]

# ASCII sparkline ladder (low -> high).
_SPARK_CHARS = "_.,-=*#"
