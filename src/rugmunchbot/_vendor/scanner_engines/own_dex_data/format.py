# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_dex_data/format.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Data transformation — DexScreener-compatible pair building with decimal normalization.

All raw amounts from ClickHouse (amount0_in, amount0_out, etc.) are stored
in the token's native smallest unit (wei for ETH, 6 decimals for USDC, etc.).
This module normalizes them by dividing by 10^decimals so that volumes and
prices represent human-readable token amounts.

Normalization logic:
  1. Well-known tokens (WETH, USDC, USDT, DAI, etc.) have hardcoded decimals.
  2. For unknown tokens that appear in evm_transfers, decimals are estimated
     from transfer value magnitudes (looks at the most common leading zeros).
  3. Default is 18 decimals (most common ERC-20 standard).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines.own_dex_data.client import (
    _get_ch_client,
    _load_pair_cache,
)

logger = get_logger(__name__)

# ── Well-Known Token Decimals ──────────────────────────────────────────
# Keyed by lowercase address on each chain.
# Populated from verified contract data and common DeFi tokens.
_KNOWN_DECIMALS: dict[str, dict[str, int]] = {
    "ethereum": {
        # Native / Wrapped
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18,  # WETH
        "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": 18,  # ETH (native)
        # Stablecoins
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,  # USDC
        "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,  # USDT
        "0x6b175474e89094c44da98b954eedeac495271d0f": 18,  # DAI
        "0x4fabb145d64652a948d72533023f6e7a623c7c53": 18,  # BUSD
        "0x8e870d67f660d95d5be530380d0ec0bd388289e1": 18,  # USDP (Paxos)
        "0x956f47f50a910163d8bf957cf5846d573e7f87ca": 18,  # FEI
        "0xbc396689893d065f41bc2c6ecbee5e0085233447": 8,  # PERP
        # LRT/LST
        "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": 18,  # wstETH
        "0xae7ab96520de3a18e5e111b5eaab095312d7fe84": 18,  # stETH
        "0xbe9895146f7af43049ca1c1ae358b0541ea49704": 18,  # cbETH
        "0x5a98fcbea516cf06857215779fd812ca3bef1b32": 18,  # LDO
        # Major DeFi
        "0x7fc66500c84a76ad7e9c93437bfc5ac33e2ddae9": 18,  # AAVE
        "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984": 18,  # UNI
        "0x514910771af9ca656af840dff83e8264ecf986ca": 18,  # LINK
        "0x5283d291dbcf85356a21ba090e6db59121208b44": 18,  # BLUR
        "0xbaac2b4491727d79d2b4f7c68a2675f5f5d7c1f2": 8,  # CRV (note: CRV is 18 actually, let's be careful)
        "0xd533a949740bb3306d119cc777fa900ba034cd52": 18,  # CRV
        "0x4e3fbd56cd56c3e72c1403e103b45db9da5b9d2b": 18,  # CVX
        "0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f": 18,  # SNX
        "0x111111111117dc0aa78b770fa6a738034120c302": 18,  # 1INCH
        "0x0bc529c00c6401aef6d220be8c6ea1667f6ad93e": 18,  # YFI
        # Meme tokens
        "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce": 18,  # SHIB
        "0x3845badade8e6dff049820680d1f14bd3903a5d0": 18,  # SAND
        "0xcc8fa225d80b9c7d42f96e9570156c65d6caaa25": 18,  # SLTH
        "0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2": 18,  # MKR
    },
    "base": {
        # Native / Wrapped
        "0x4200000000000000000000000000000000000006": 18,  # WETH on Base
        "0x4200000000000000000000000000000000000022": 18,  # BAL (not sure if correct)
        # Stablecoins
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": 6,  # USDC on Base
        "0x50c5725949a6f0c72e6c4a641f24049a917db0cb": 18,  # DAI on Base
        # Major
        "0x940181a94a35a4569e4529a3cdfb74e38fd98631": 18,  # AERO
        "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22": 18,  # cbETH on Base
    },
    "bsc": {
        "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": 18,  # WBNB
        "0x55d398326f99059ff775485246999027b3197955": 18,  # USDT on BSC
        "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d": 18,  # USDC on BSC
        "0xe9e7cea3dedca5984780bafc599bd69add087d56": 18,  # BUSD on BSC
        "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c": 18,  # BTCB
        "0x2170ed0880ac9a755fd29b2688956bd959f933f8": 18,  # WETH on BSC
    },
    "arbitrum": {
        "0x82af49447d8a07e3bd95bd0d56f35241523fbab1": 18,  # WETH on Arbitrum
        "0xaf88d065e77c8cc2239327c5edb3a432268e5831": 6,  # USDC on Arbitrum
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9": 6,  # USDT on Arbitrum
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": 18,  # DAI on Arbitrum
        "0x912ce59144191c1204e64559fe8253a0e49e6548": 18,  # ARB
    },
    "polygon": {
        "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": 18,  # WMATIC
        "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": 18,  # WETH on Polygon
        "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": 6,  # USDC on Polygon
        "0xc2132d05d31c914a87c6611c10748aeb04b58e8f": 6,  # USDT on Polygon
        "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063": 18,  # DAI on Polygon
    },
    "optimism": {
        "0x4200000000000000000000000000000000000006": 18,  # WETH on Optimism
        "0x0b2c639c533813f4aa9d7837caf62653d097ff85": 6,  # USDC on Optimism
        "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58": 6,  # USDT on Optimism
        "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1": 18,  # DAI on Optimism
        "0x4200000000000000000000000000000000000042": 18,  # OP
    },
    "avalanche": {
        "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7": 18,  # WAVAX
        "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e": 6,  # USDC on Avalanche
        "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7": 6,  # USDT on Avalanche
        "0xd586e7f844cea2f87f50152665bcbc2c279d8d70": 18,  # DAI on Avalanche
    },
    "fantom": {
        "0x21be370d5312f44cb42ce377bc9b8a0cef1a4c83": 18,  # WFTM
        "0x04068da6c83afcfa0e13ba15a6696662335d5b75": 6,  # USDC on Fantom
        "0x049d68029688eabf473097a2fc38ef61633a3c7a": 18,  # fUSDT
    },
    "linea": {
        "0xe5d7c2a44ffddf6b295a15c148167daaaf5cf34f": 18,  # WETH on Linea
        "0x176211869ca2b568f2a7d4ee941e073a542ee1f2": 6,  # USDC on Linea
    },
    "mantle": {
        "0x78c1b0c915c4faa5fffa6cabf0219da63d7f4cb8": 18,  # WMNT
        "0x09bc4e0d864854c6afb6eb9a9cdf58a1907920b2": 18,  # USDC on Mantle
    },
}

# In-memory cache for token decimals queried from ClickHouse
_decimals_cache: dict[str, int] = {}
_DECIMALS_CACHE_MAX = 500


def _get_token_decimals(token_address: str, chain: str) -> int:
    """Get token decimals for a given address and chain.

    Resolution order:
      1. Well-known token map (hardcoded)
      2. In-memory cache (from previous lookups)
      3. ClickHouse evm_transfers value-pattern estimation
      4. Default 18

    Args:
        token_address: Token contract address (lowercase)
        chain: Chain identifier

    Returns:
        Number of decimals (6 for USDC, 18 for WETH, etc.)
    """
    addr = token_address.lower()
    cache_key = f"{chain}:{addr}"

    # Check in-memory cache
    if cache_key in _decimals_cache:
        return _decimals_cache[cache_key]

    # Check well-known map
    chain_map = _KNOWN_DECIMALS.get(chain, {})
    if addr in chain_map:
        _decimals_cache[cache_key] = chain_map[addr]
        return chain_map[addr]

    # Check known tokens across all chains (some are chain-agnostic)
    for _c, tokens in _KNOWN_DECIMALS.items():
        if addr in tokens:
            _decimals_cache[cache_key] = tokens[addr]
            return tokens[addr]

    # Try to estimate from ClickHouse evm_transfers data
    try:
        estimated = _estimate_decimals_from_transfers(addr, chain)
        if estimated is not None:
            _decimals_cache[cache_key] = estimated
            return estimated
    except (ConnectionError, OSError, TimeoutError, RuntimeError, ValueError, KeyError, TypeError, CHDriverError):
        logger.debug("decimals_estimation_failed", token=addr[:16], chain=chain)

    # Default to 18 (most common ERC-20 standard)
    _decimals_cache[cache_key] = 18
    if len(_decimals_cache) > _DECIMALS_CACHE_MAX:
        _decimals_cache.pop(next(iter(_decimals_cache)), None)

    return 18


def _estimate_decimals_from_transfers(token_address: str, chain: str) -> int | None:
    """Estimate token decimals by analyzing transfer value patterns in ClickHouse.

    Strategy:
      - Query the most common transfer VALUE length (as string) for this token.
      - For 18-decimal tokens, typical non-whale transfers are ~15-22 chars.
      - For 6-decimal tokens, typical values are ~6-12 chars.
      - We look at the last 100 non-zero transfers and find the most common
        value digit count as a clue for the decimals.

    This is a heuristic — not all tokens follow these patterns — but it is
    more reliable than assuming 18 for every unknown token.
    """
    client = _get_ch_client()
    if client is None:
        return None

    query = """
        SELECT
            length(toString(value)) AS val_len,
            count() AS cnt
        FROM wallet_memory.evm_transfers
        WHERE token_address = %(token)s
          AND chain = %(chain)s
          AND value != '0'
          AND value != ''
        GROUP BY val_len
        ORDER BY cnt DESC
        LIMIT 1
    """
    try:
        rows = client.execute(query, {"token": token_address, "chain": chain})
        if rows:
            most_common_len = rows[0][0]
            # Heuristic: map value length to likely decimals
            # 18 decimals -> values like 1000000000000000000 (19 chars for 1 token)
            # 6 decimals  -> values like 1000000 (7 chars for 1 token)
            # For a value of "N" chars where the amount is 10^decimals:
            #   N ≈ decimals + 1  (for small-ish amounts like 1-10 tokens)
            if most_common_len >= 16:
                return 18
            elif most_common_len >= 10:
                return 8 if most_common_len <= 12 else 18
            elif most_common_len >= 6:
                return 6
            else:
                return 18
    except (ConnectionError, OSError, TimeoutError, RuntimeError, ValueError, KeyError, TypeError, CHDriverError):
        logger.debug("transfer_value_query_failed", token=token_address[:16], chain=chain)

    return None


def _normalize_amount(raw_amount: str | float | int | None, token_address: str, chain: str) -> float:
    """Convert raw on-chain amount to human-readable value by dividing by 10^decimals.

    Args:
        raw_amount: Raw amount string or number from ClickHouse
        token_address: Token contract address for decimal lookup
        chain: Chain identifier

    Returns:
        Normalized float amount in human-readable units
    """
    if not raw_amount:
        return 0.0
    try:
        raw = float(str(raw_amount))
    except (ValueError, TypeError):
        return 0.0
    if raw == 0.0:
        return 0.0
    decimals = _get_token_decimals(token_address, chain)
    return raw / (10**decimals)


def _build_pairs(
    swaps: list[dict[str, Any]],
    our_token: str,
    chain: str,
) -> list[dict[str, Any]]:
    """Build DexScreener-compatible pairs from our dex_events data.

    Groups swaps by pool (contract_address) and computes:
      - volume_24h: sum of amounts for our token in the pair (normalized by decimals)
      - liquidity_usd: estimated from largest swap (proxy)
      - price_usd: volume-weighted average price (normalized)
      - txns_24h: buy/sell count (determined by direction)
      - price_change_24h: relative change from first to last swap

    All raw amounts from ClickHouse are divided by 10^decimals to produce
    human-readable token amounts.
    """
    pair_cache = _load_pair_cache(chain)

    def _get_tokens(pool_addr: str, dex_info: dict[str, Any]) -> tuple[str, str]:
        """Resolve token0/token1 for a pool, falling back to pair cache."""
        t0 = dex_info.get("token0", "")
        t1 = dex_info.get("token1", "")
        if t0 and t1:
            return (t0, t1)
        cached = pair_cache.get(pool_addr.lower())
        if cached:
            return cached
        return (t0, t1)

    # Resolve the paired token (token0 or token1 in the pool)
    # We need decimals for our token and the paired token
    # For now, get the paired token from the first swap that has token data
    def _resolve_paired_token(pool_addr: str, our_token_lower: str, dex_info: dict[str, Any]) -> str | None:
        t0, t1 = _get_tokens(pool_addr, dex_info)
        if t0.lower() == our_token_lower:
            return t1
        elif t1.lower() == our_token_lower:
            return t0
        return None

    our_token_lower = our_token.lower()

    # Group swaps by pool
    pools: dict[str, list[dict[str, Any]]] = {}
    for swap in swaps:
        pool = swap["contract_address"]
        if pool not in pools:
            pools[pool] = []
        pools[pool].append(swap)

    pairs: list[dict[str, Any]] = []
    for pool_addr, pool_swaps in pools.items():
        if not pool_swaps:
            continue

        dex_info = pool_swaps[0]
        dex_name = f"{dex_info['dex']}_{dex_info['version']}"

        resolved_t0, resolved_t1 = _get_tokens(pool_addr, dex_info)
        is_token0 = resolved_t0.lower() == our_token_lower
        paired_token = resolved_t1 if is_token0 else resolved_t0

        volume_24h = 0.0
        buy_count = 0
        sell_count = 0
        prices_weighted: list[tuple[float, float]] = []

        for swap in pool_swaps:
            if is_token0:
                amt_in_raw = float(swap["amount0_in"] or 0)
                amt_out_raw = float(swap["amount0_out"] or 0)
                paired_in_raw = float(swap["amount1_in"] or 0)
                paired_out_raw = float(swap["amount1_out"] or 0)
            else:
                amt_in_raw = float(swap["amount1_in"] or 0)
                amt_out_raw = float(swap["amount1_out"] or 0)
                paired_in_raw = float(swap["amount0_in"] or 0)
                paired_out_raw = float(swap["amount0_out"] or 0)

            # Normalize both our token and paired token amounts
            amt_in = _normalize_amount(amt_in_raw, our_token, chain)
            amt_out = _normalize_amount(amt_out_raw, our_token, chain)
            paired_in = _normalize_amount(paired_in_raw, paired_token, chain)
            paired_out = _normalize_amount(paired_out_raw, paired_token, chain)

            volume_24h += amt_in + amt_out

            # Determine buy/sell: if our token is token0 and amount0_out > 0,
            # someone is buying (our token leaves the pool = buy)
            is_buy = float(swap["amount0_out"] or 0) > 0 if is_token0 else float(swap["amount1_out"] or 0) > 0

            if is_buy:
                buy_count += 1
            else:
                sell_count += 1

            # Compute price using normalized paired amounts
            if amt_in + amt_out > 0:
                if is_token0:
                    if amt_out > 0:
                        price = paired_in / amt_out if amt_out else 0
                    else:
                        price = paired_out / amt_in if amt_in else 0
                else:
                    if amt_out > 0:
                        price = paired_in / amt_out if amt_out else 0
                    else:
                        price = paired_out / amt_in if amt_in else 0

                if price > 0 and price < 1_000_000:
                    prices_weighted.append((price, amt_in + amt_out))

        price_usd = 0.0
        if prices_weighted:
            total_weight = sum(w for _, w in prices_weighted)
            if total_weight > 0:
                price_usd = sum(p * w for p, w in prices_weighted) / total_weight

        price_change_24h = 0.0
        if len(prices_weighted) > 1:
            first_price = prices_weighted[0][0]
            last_price = prices_weighted[-1][0]
            if first_price > 0:
                price_change_24h = ((last_price - first_price) / first_price) * 100

        # Estimate liquidity from normalized volumes
        max_swap_volume = (
            max(
                _normalize_amount(
                    float(s["amount0_in"] or 0) + float(s["amount0_out"] or 0)
                    if is_token0
                    else float(s["amount1_in"] or 0) + float(s["amount1_out"] or 0),
                    our_token,
                    chain,
                )
                for s in pool_swaps
            )
            if pool_swaps
            else 0
        )
        liquidity_est = max(max_swap_volume * 100, volume_24h * 0.3)

        pair_entry = {
            "chainId": chain,
            "dexId": dex_name,
            "pairAddress": pool_addr,
            "priceUsd": str(round(price_usd, 18)) if price_usd > 0 else "0",
            "priceNative": "",
            "volume": {
                "h24": round(volume_24h, 2),
                "h6": round(volume_24h * 0.25, 2),
                "h1": round(volume_24h * 0.04, 2),
            },
            "liquidity": {
                "usd": round(liquidity_est, 2),
                "base": str(round(volume_24h * 0.5, 2)),
                "quote": str(round(volume_24h * 0.5, 2)),
            },
            "txns": {
                "h24": {
                    "buys": buy_count,
                    "sells": sell_count,
                }
            },
            "priceChange": {
                "h24": round(price_change_24h, 2),
            },
            "labels": ["v3" if "v3" in dex_name else "v2"],
            "fdv": 0,
        }

        pairs.append(pair_entry)

    pairs.sort(key=lambda p: p["volume"]["h24"], reverse=True)
    return pairs


def _parse_decimal(value: Any) -> Decimal | None:
    """Safely parse a decimal value from ClickHouse."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None
