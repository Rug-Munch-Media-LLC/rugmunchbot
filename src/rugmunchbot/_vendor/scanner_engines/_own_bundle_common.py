# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_own_bundle_common.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Shared plumbing for own_bundle_detect (degodding split 2026-08-13).

Holds the lazy ClickHouse client, chain normalisation, cache state, and the
query/scoring constants shared by ``own_bundle_detect.py`` (public API),
``_own_bundle_queries.py`` (CH queries), and ``_own_bundle_scoring.py``
(pure scoring). ``own_bundle_detect.py`` re-exports these names so
``bd._get_ch_client`` / ``bd._ch_client`` / ``bd._normalize_chain`` etc.
keep resolving for tests and callers.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_BUNDLE_CACHE: dict[str, dict[str, Any]] = {}
_BUNDLE_CACHE_TTL = 600  # 10 minutes
_BUNDLE_CACHE_MAX = 100

# ── Launch-window bundle detection constants ─────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10  # time-box for ClickHouse queries
_LAUNCH_WINDOW_BLOCKS = 5  # first N blocks after deployment
_SIMILAR_AMOUNT_CV_BANDS = ((0.1, 100.0), (0.3, 80.0), (0.5, 50.0), (0.8, 20.0))

# ── Lazy ClickHouse client ────────────────────────────────────────────────
_ch_client: Any = None

# ── Chain normalization ───────────────────────────────────────────────────
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
    "solana": "solana",
}


def _normalize_chain(chain: str) -> str:
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native protocol client."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "")
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
        logger.info("Own bundle detect ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own bundle detect ClickHouse unavailable: %s", e)
        _ch_client = None
        return None
