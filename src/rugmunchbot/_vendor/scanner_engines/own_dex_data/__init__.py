# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/own_dex_data/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own DEX Data package — split from own_dex_data.py for maintainability.

Phase 1 of the Data Ownership Strategy.
Eliminates 111 DexScreener API calls per token scan by querying
chain-indexer ClickHouse dex_events (47M+ DEX swap events).
"""

from rugmunchbot._vendor.scanner_engines.own_dex_data.client import (
    clear_cache,
    get_cache_stats,
)
from rugmunchbot._vendor.scanner_engines.own_dex_data.query import (
    _query_dex_swaps,
    _query_pool_stats,
    check_data_freshness,
    get_own_dex_data,
    get_token_price_simple,
)

__all__ = [
    "_query_dex_swaps",
    "_query_pool_stats",
    "check_data_freshness",
    "clear_cache",
    "get_cache_stats",
    "get_own_dex_data",
    "get_token_price_simple",
]
