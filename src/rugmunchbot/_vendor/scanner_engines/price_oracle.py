# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/price_oracle.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
F2 Price / Decimals Oracle — USD Normalization Layer
=====================================================
Every RugMunchScan capability that computes USD values depends on this
module: wallet flow (C1), holder clustering (C2), attack detection (C6),
and deployer fingerprint (C7).

Tiered price resolution:
  1. OWN DATA: derive price from wallet_memory.dex_events reserve ratios
     (amount1_out / amount0_in for any pair we've indexed — 59M rows, free).
     Ratios are decimals-normalized and anchored to USD via stablecoin or
     native-wrapped pairs.
  2. FREE APIs (verified 2026-08-02, no auth):
       DexScreener  -> https://api.dexscreener.com/latest/dex/tokens/{addr}
       CoinGecko    -> https://api.coingecko.com/api/v3/simple/token_price/{platform}
       DeFiLlama    -> https://coins.llama.fi/prices/current/{chain}:{addr}
       GeckoTerminal-> https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{addr}
       ParaSwap     -> https://apiv5.paraswap.io/prices (free, no auth)
       (1inch is FREEMIUM — only used when ONEINCH_API_KEY is set)
  3. Cache: Redis (`price:{chain}:{address}`, TTL 300s) + ClickHouse
     `wallet_memory.token_meta` table (ReplacingMergeTree).

Public API:
    price = await get_token_price("0x...", "ethereum")
    # -> {"price_usd": 0.001234, "source": "own_dex_events",
    #     "timestamp": 1720000000, "confidence": 0.85}
    decimals = await get_token_decimals("0x...", "ethereum")     # 6 / 18 / ...
    native = await get_native_price("ethereum")                  # USD per ETH
    usd = await normalize_amount("0x...", "ethereum", 1_000_000) # -> USD

All functions are defensive: type-hinted, docstringed, wrapped in
try/except, and fail gracefully (return None / 0.0) when ClickHouse or
the free APIs are unreachable.

NOTE (degodding 2026-08-14): this module is now a thin re-export shim. The
source-level fetch / storage layer lives in ``engines/_sources.py``
(ClickHouse + Redis clients, token_meta read/write, in-process caches,
cache layers, OWN-DATA dex_events tiers, historical swap queries,
``get_cache_stats`` / ``clear_cache``) and the aggregation / tier-selection
/ fallback layer lives in ``engines/_oracle.py`` (``_TIERS``,
``get_token_price``, ``get_token_price_at_time``, ``get_token_decimals``,
``get_native_price``, ``normalize_amount``). The public names and signatures
are preserved so the ~10 callers are unaffected.
"""

from __future__ import annotations

from rugmunchbot._vendor.scanner_engines._oracle import (
    get_native_price,
    get_token_decimals,
    get_token_price,
    get_token_price_at_time,
    normalize_amount,
)
from rugmunchbot._vendor.scanner_engines._sources import (
    clear_cache,
    ensure_token_meta_table,
    get_cache_stats,
)

__all__ = [
    "clear_cache",
    "ensure_token_meta_table",
    "get_cache_stats",
    "get_native_price",
    "get_token_decimals",
    "get_token_price",
    "get_token_price_at_time",
    "normalize_amount",
]
