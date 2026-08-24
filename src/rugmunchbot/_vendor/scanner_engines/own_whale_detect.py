# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/own_whale_detect.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Whale Detection — ClickHouse-Powered Large Flow Analysis
=============================================================
Phase 4 of the Data Ownership Strategy.

Detects whale wallets and their behavior for a token from our own
ClickHouse data:
  - Large swaps (>$10K equivalent) from dex_events
  - Wallet-level aggregation: bought_usd, sold_usd, net flow, tx count
  - Behavior classification: accumulating vs dumping vs cycling
  - Whale concentration score (how much of large flow a few wallets control)

Architecture:
  - Reuses own_dex_data swap queries + the F2 price oracle (price_oracle.py)
  - Converts raw on-chain amounts to USD via token decimals + USD price
  - Returns a whale concentration score and per-wallet flags
  - Falls back gracefully when data or price is insufficient

Usage:
    from rugmunchbot._vendor.scanner_engines.own_whale_detect import get_own_whale_detection

    result = await get_own_whale_detection("0x...", "ethereum")
    # result = {
    #     "whale_count": 3,
    #     "whale_concentration_score": 62.5,
    #     "whale_wallets": [
    #         {"address": "0x...", "behavior": "accumulating",
    #          "bought_usd": 120000.0, "sold_usd": 20000.0, "net_usd": 100000.0},
    #     ],
    #     "flags": ["whale_accumulation"],
    #     "large_flow_usd": 150000.0,
    #     "confidence": 0.7,
    #     "source": "own",
    # }

Data Sources:
  Primary: wallet_memory.dex_events (swap events)
  Primary: wallet_memory.evm_transfers (large transfer events, fallback)
  Price:   F2 price oracle (own dex_events TWAP -> DexScreener -> CoinGecko ->
           DeFiLlama -> GeckoTerminal -> ParaSwap/1inch)
"""

from __future__ import annotations

import math
import os
import time
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
)
from rugmunchbot._vendor.scanner_engines.own_dex_data.query import _query_dex_swaps

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_WHALE_CACHE: dict[str, dict[str, Any]] = {}
_WHALE_CACHE_TTL = 300  # 5 minutes — whale flows change fast but scans don't re-run often
_WHALE_CACHE_MAX = 100

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

# Default USD threshold for "whale" classification
DEFAULT_MIN_USD = 10_000.0


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to the format used in ClickHouse tables."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native protocol client for wallet_memory database."""
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
        logger.info("Own whale detect ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own whale detect ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_whale_detection(
    token_address: str,
    chain: str,
    min_usd_threshold: float = DEFAULT_MIN_USD,
    lookback_hours: int = 168,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Detect whale wallets and their behavior for a token.

    Queries dex_events for swaps in the lookback window, converts amounts
    to USD via the token TWAP from own_dex_data, then aggregates per wallet.

    Args:
        token_address: Token contract address
        chain: Chain name (ethereum, bsc, etc.)
        min_usd_threshold: USD threshold for whale classification
        lookback_hours: Lookback window (default 7 days)
        force_fresh: Bypass cache

    Returns:
        Dict with whale wallets, concentration score, and flags, or an
        error dict when insufficient data.
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"{token_address}:{chain}:{lookback_hours}:{min_usd_threshold}"
    if not force_fresh and cache_key in _WHALE_CACHE:
        cached = _WHALE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _WHALE_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "whale_detection",
        "result": "scanned",
        "source": "own",
        "whale_count": 0,
        "whale_concentration_score": 0.0,
        "whale_wallets": [],
        "flags": [],
        "large_flow_usd": 0.0,
        "total_swaps_analyzed": 0,
        "price_usd": 0.0,
        "confidence": 0.0,
    }

    try:
        # ── 1. Get USD price + decimals via the F2 price oracle ──
        from rugmunchbot._vendor.scanner_engines.price_oracle import (
            get_token_decimals,
            get_token_price,
        )

        price_result = await get_token_price(token_address, chain)
        price_usd = float(price_result.get("price_usd", 0) or 0) if price_result else 0.0
        price_confidence = float(price_result.get("confidence", 0) or 0) if price_result else 0.0
        decimals = await get_token_decimals(token_address, chain)
        if decimals is None:
            decimals = 18
        result["price_usd"] = price_usd
        result["price_confidence"] = price_confidence
        result["price_source"] = price_result.get("source", "") if price_result else ""

        # ── 2. Fetch swap events ──
        swaps = await _query_dex_swaps(token_address, chain, hours_back=lookback_hours)
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("whale_query_failed for %s: %s", token_address[:16], exc)
        result["result"] = "unavailable"
        return result

    if not swaps:
        logger.debug("no swaps for whale detection on %s", token_address[:16])
        result["result"] = "unavailable"
        return result

    result["total_swaps_analyzed"] = len(swaps)

    def _raw_to_usd(raw_amount: Any) -> float:
        """Convert a raw on-chain amount to USD via decimals + oracle price."""
        raw: float = 0.0
        try:
            raw = float(raw_amount or 0)
        except (ValueError, TypeError):
            return 0.0
        if raw <= 0:
            return 0.0
        human = raw / math.pow(10, decimals)
        return human * price_usd

    # ── 3. Convert swaps to USD flows and attribute to wallets ──
    wallet_flows: dict[str, dict[str, Any]] = {}
    large_flow_usd = 0.0
    for swap in swaps:
        try:
            is_token0 = (swap.get("token0") or "").lower() == token_address
            if is_token0:
                usd = _raw_to_usd(swap.get("amount0_out") or 0)
                is_buy = usd > 0
                if not is_buy:
                    usd = _raw_to_usd(swap.get("amount0_in") or 0)
            else:
                usd = _raw_to_usd(swap.get("amount1_out") or 0)
                is_buy = usd > 0
                if not is_buy:
                    usd = _raw_to_usd(swap.get("amount1_in") or 0)
        except (ValueError, TypeError):
            continue

        if usd < min_usd_threshold:
            continue

        large_flow_usd += usd

        # Attribute to sender (the trader) and, if different, recipient
        actors: list[str] = []
        sender = (swap.get("sender") or "").lower()
        recipient = (swap.get("recipient") or "").lower()
        if sender:
            actors.append(sender)
        if recipient and recipient != sender:
            actors.append(recipient)

        for actor in actors:
            if actor in ("", "0x0000000000000000000000000000000000000000"):
                continue
            flow = wallet_flows.setdefault(
                actor,
                {"address": actor, "bought_usd": 0.0, "sold_usd": 0.0, "net_usd": 0.0, "tx_count": 0},
            )
            if is_buy:
                flow["bought_usd"] += usd
                flow["net_usd"] += usd
            else:
                flow["sold_usd"] += usd
                flow["net_usd"] -= usd
            flow["tx_count"] += 1

    if not wallet_flows or large_flow_usd <= 0:
        result["result"] = "unavailable"
        return result

    # ── 4. Classify behavior + build output ──
    whale_wallets: list[dict[str, Any]] = []
    for addr, flow in wallet_flows.items():
        net = flow["net_usd"]
        if net > 0:
            behavior = "accumulating"
        elif net < 0:
            behavior = "dumping"
        else:
            behavior = "cycling"
        whale_wallets.append(
            {
                "address": addr,
                "behavior": behavior,
                "bought_usd": round(flow["bought_usd"], 2),
                "sold_usd": round(flow["sold_usd"], 2),
                "net_usd": round(net, 2),
                "tx_count": flow["tx_count"],
            }
        )

    whale_wallets.sort(key=lambda w: abs(w["net_usd"]), reverse=True)
    result["whale_wallets"] = whale_wallets
    result["whale_count"] = len(whale_wallets)
    result["large_flow_usd"] = round(large_flow_usd, 2)

    # ── 5. Concentration score: top whale net flow share of large flow ──
    top_net = sum(max(w["net_usd"], 0) for w in whale_wallets[:3])
    result["whale_concentration_score"] = round((top_net / large_flow_usd) * 100, 1)

    # ── 6. Flags ──
    net_all = sum(w["net_usd"] for w in whale_wallets)
    if net_all > 0.3 * large_flow_usd:
        result["flags"].append("whale_accumulation")
    if net_all < -0.3 * large_flow_usd:
        result["flags"].append("whale_dump_pressure")
    if any(w["behavior"] == "cycling" for w in whale_wallets) and len(whale_wallets) >= 2:
        result["flags"].append("possible_wash_cycling")

    result["confidence"] = round(min(1.0, price_confidence + 0.2), 2)

    _cache_whale(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_whale_detect_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        whales=result["whale_count"],
        flow_usd=round(large_flow_usd, 2),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_whale(key: str, data: dict[str, Any]) -> None:
    """Cache whale detection data with timestamp."""
    data["_cached_at"] = time.time()
    _WHALE_CACHE[key] = data
    if len(_WHALE_CACHE) > _WHALE_CACHE_MAX:
        _WHALE_CACHE.pop(next(iter(_WHALE_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _WHALE_CACHE,
        _WHALE_CACHE_MAX,
        _WHALE_CACHE_TTL,
        extra={"clickhouse_connected": _ch_client is not None},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_WHALE_CACHE)
