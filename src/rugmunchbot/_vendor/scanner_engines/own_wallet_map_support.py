# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_wallet_map_support.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Shared constants/primitives for own-wallet-map (de-godding 2026-08-23)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
)
_WALLET_MAP_CACHE_TTL = 300  # 5 minutes — holder data changes slowly

_WALLET_MAP_CACHE_MAX = 100

ROLE_EMOJI: dict[str, str] = {
    "dev": "\U0001f6e0\ufe0f",  # 🛠️
    "whale": "\U0001f433",  # 🐳
    "fish": "\U0001f41f",  # 🐟
    "shrimp": "\U0001f964",  # 🍤
    "fresh": "\U0001f331",  # 🌱
    "bundle": "\U0001f4e6",  # 📦
}

_ROLE_ORDER: tuple[str, ...] = ("dev", "whale", "fish", "shrimp", "fresh", "bundle")

_FRESH_WALLET_DAYS = 7

_FRESH_WALLET_HOURS = 24

_RECENT_FUNDED_HOURS = 6

_WHALE_PCT = 5.0

_FISH_PCT = 1.0

_MAX_HOLDERS = 20

_KNOWN_ROUTER_WALLETS: frozenset[str] = frozenset(
    {
        # Uniswap V2 Router 02 (Ethereum)
        "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
        # Uniswap V3 SwapRouter / SwapRouter02 (Ethereum, Arbitrum, ...)
        "0xe592427a0aece92de3edee1f18e0157c05861564",
        "0x68b3465833fb72a70ecdf485e0e4c7bd8665fc45",
        # SwapRouter02 (Base)
        "0x2626664c2603336e57b271c5c0b26f421741e481",
        # SushiSwap Router
        "0xd9e1ce17f2641f24ae83637ab66a2cca9c378b9f",
        # PancakeSwap V2 Router / V3 SwapRouter (BSC)
        "0x10ed43c718714eb63d5aa57b78b54704e256024e",
        "0x13f4ea83d0bd40e75c8222255bc855a974568dd4",
        # 1inch aggregator routers (v4 / v5-v6)
        "0x1111111254fb6c44bac0bed2854e76f90643097d",
        "0x1111111254eeb25477b68fb85ed929f73a960582",
        # 0x (ZRX) Exchange Proxy
        "0xdef1c0ded9bec7f1a1670819833240f027b25eff",
        # Quickswap V2 / V3 (Polygon)
        "0xa5e0829caced8ffdd4de3c43696c57f7d7a678ff",
        "0xf5b509bb0909a69b1c207e495f45e8c9d590a6e9",
        # Camelot Router (Arbitrum)
        "0xc873fecbd354f5a56e00e710b90ef4201db2448d",
        # Solana: Jupiter v6 / Raydium (AmmV4 / CLMM) / Pump.fun programs
        "jup6lkzbzsj1jkkwapdhny74zcz3tluzoi5qnyvtav4",
        "675kpx9mhtjs2zt1qfr1nyhuzlexfqm9h24wfsut1mp8",
        "cammczo5yl8w4vff8kvhrk22ggusp5vta7grrkgrwqk",
        "6ef8rrecthr5dkzon8nwu78hrvfckubj14m5ubewf6p",
    }
)

def _normalize_chain(chain: str) -> str:
    """Normalize chain name to the format used in ClickHouse tables."""
    return (chain or "").lower().strip()

def _is_fresh(first_ts: Any, cutoff: datetime) -> bool:
    """True when the wallet's first on-chain tx is newer than ``cutoff``."""
    if isinstance(first_ts, datetime):
        naive = first_ts.replace(tzinfo=None) if first_ts.tzinfo else first_ts
        return naive > cutoff
    try:
        parsed = datetime.fromisoformat(str(first_ts).replace("Z", "+00:00"))
        naive = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        return naive > cutoff
    except (TypeError, ValueError):
        return False

def _to_iso(value: Any) -> str | None:
    """ISO string for datetime values, else None (graceful)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)

def _to_naive_dt(value: Any) -> datetime | None:
    """Normalize a ClickHouse/ISO datetime to naive UTC (None on garbage)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except (TypeError, ValueError):
        return None

def _hours_between(earlier: Any, later: Any) -> float | None:
    """Whole hours from ``earlier`` to ``later`` (None when either is missing)."""
    a, b = _to_naive_dt(earlier), _to_naive_dt(later)
    if a is None or b is None:
        return None
    try:
        return (b - a).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None

_CEX_DISPLAY: dict[str, str] = {
    "binance": "Binance",
    "coinbase": "Coinbase",
    "kraken": "Kraken",
    "okx": "OKX",
    "okex": "OKX",
    "bybit": "Bybit",
    "kucoin": "KuCoin",
    "gateio": "Gate.io",
    "gate_io": "Gate.io",
    "mexc": "MEXC",
    "huobi": "Huobi",
    "htx": "HTX",
    "crypto.com": "Crypto.com",
    "crypto_com": "Crypto.com",
    "bitfinex": "Bitfinex",
    "gemini": "Gemini",
    "ftx": "FTX",
    "labeled_cex": "CEX",
}
