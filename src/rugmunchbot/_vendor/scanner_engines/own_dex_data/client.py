# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_dex_data/client.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""ClickHouse client, pair cache, chain normalization, and cache management."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── DexScreener-compatible response cache ──
_DEX_CACHE: dict[str, dict[str, Any] | None] = {}
_DEX_CACHE_MAX = 100

# ── Lazy ClickHouse client ──
_ch_client: Any = None
_ch_pair_cache: dict[str, dict[str, tuple[str, str]]] = {}

# Pair cache directory (from lite_indexer)
_PAIR_CACHE_DIR = Path(os.getenv("PAIR_CACHE_DIR", "/var/lib/chain-indexer/state"))


def _load_pair_cache(chain: str) -> dict[str, tuple[str, str]]:
    """Load the pair→tokens cache for a chain.

    Built by lite_indexer from PairCreated/PoolCreated events + eth_call fallback.
    Allows resolving existing dex_events by pool address (contract_address)
    since all 47M+ historical events have empty token0/token1.
    """
    global _ch_pair_cache
    if chain in _ch_pair_cache:
        return _ch_pair_cache[chain]

    cache_path = _PAIR_CACHE_DIR / f"pair_tokens_{chain}.json"
    if not cache_path.exists():
        _ch_pair_cache[chain] = {}
        logger.debug("pair_cache_not_found", chain=chain)
        return {}

    try:
        data = json.loads(cache_path.read_text())
        result = {k: (v[0], v[1]) if isinstance(v, list) else v for k, v in data.items()}
        _ch_pair_cache[chain] = result
        logger.debug("pair_cache_loaded", chain=chain, pairs=len(result))
        return result
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        logger.warning("pair_cache_load_failed", chain=chain, error=str(exc)[:80])
        _ch_pair_cache[chain] = {}
        return {}


def _get_pools_for_token(pair_cache: dict[str, tuple[str, str]], token_address: str) -> list[str]:
    """Reverse-lookup: find all pool addresses where token0 or token1 matches."""
    token_lower = token_address.lower()
    pools: list[str] = []
    for pool_addr, (t0, t1) in pair_cache.items():
        if t0.lower() == token_lower or t1.lower() == token_lower:
            pools.append(pool_addr)
    return pools


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client for wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        _ch_client.execute("SELECT 1")
        logger.info("Own DEX data ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own DEX data ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


async def _run_sync(fn, *args, **kwargs):
    """Run a sync function in a thread pool (clickhouse_driver is sync)."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


# ── Chain name normalization ──
_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "solana": "solana",
}


def _normalize_chain(chain: str) -> str:
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


# ── Metrics ──


def get_cache_stats() -> dict[str, Any]:
    """Return current cache statistics for monitoring."""
    return {
        "cache_size": len(_DEX_CACHE),
        "cache_max": _DEX_CACHE_MAX,
        "clickhouse_connected": _ch_client is not None,
    }


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _DEX_CACHE.clear()
