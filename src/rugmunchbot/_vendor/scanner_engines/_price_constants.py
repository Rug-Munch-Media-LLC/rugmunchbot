# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_price_constants.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pure constants for the price/decimals oracle (Batch 13 degodding).

Extracted from ``price_oracle.py``: cache TTLs, wrapped-native address map,
multi-chain stablecoin allowlist, and the decimals fallback table. No
imports beyond the standard library — safe to import anywhere without
pulling in the oracle's ClickHouse/Redis/httpx stack.
"""

from __future__ import annotations

# ── Cache TTLs ─────────────────────────────────────────────────────────────
_PRICE_TTL = 300  # Redis + token_meta price cache TTL (seconds)
_MEM_TTL = 60  # in-process price cache TTL (seconds)
_MEM_MAX = 500  # in-process cache max entries (LRU eviction)
_NATIVE_TTL = 300  # native price cache TTL (seconds)

# ── Wrapped native token addresses per chain (for native price resolution) ──
_WRAPPED_NATIVE: dict[str, str] = {
    "ethereum": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
    "bsc": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
    "polygon": "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",  # WMATIC
    "arbitrum": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
    "optimism": "0x4200000000000000000000000000000000000006",  # WETH
    "base": "0x4200000000000000000000000000000000000006",  # WETH
    "avalanche": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",  # WAVAX
    "fantom": "0x21be370d5312f44cb42ce377bc9b8a0cef1a4c83",  # WFTM
    "linea": "0xe5d7c2a44ffddf6b295a15c148167daaaf5cf34f",  # WETH
    "gnosis": "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d",  # WXDAI
    "mantle": "0x78c1b0c915c4faa5fffa6cabf0219da63d7f4cb8",  # WMNT
    "solana": "So11111111111111111111111111111111111111112",  # WSOL
}

# ── Stablecoin addresses (multi-chain) used to anchor own-data prices to USD ──
_STABLECOINS: set[str] = {
    # ethereum
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
    "0x4fabb145d64652a948d72533023f6e7a623c7c53",  # BUSD
    "0x853d955acef822db058eb8505911ed77f175b99e",  # FRAX
    "0x57ab1ec28d129707052df4df418d58a2d46d5f51",  # USDS (sUSDS)
    # base
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    # arbitrum
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
    # optimism
    "0x0b2c639c533813f4aa9d7837caf62653d097ff85",  # USDC
    "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",  # USDT
    "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1",  # DAI
    # polygon
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",  # USDC
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",  # USDT
    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",  # DAI
    # avalanche
    "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",  # USDC
    "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",  # USDT
    # fantom
    "0x04068da6c83afcfa0e13ba15a6696662335d5b75",  # USDC
    # bsc
    "0x55d398326f99059ff775485246999027b3197955",  # USDT
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
    # solana
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KjNkEN6kiFnSkHM6hTzM",  # USDT
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # USDe
}

# ── Decimals fallback map (spec: ETH=18, USDC=6, USDT=6, SOL=9) ────────────
# Layered over own_dex_data.format._get_token_decimals which already covers
# a wide known-token table + ClickHouse value-pattern estimation.
_FALLBACK_DECIMALS: dict[str, dict[str, int]] = {
    "solana": {
        "So11111111111111111111111111111111111111112": 9,  # WSOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 6,  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KjNkEN6kiFnSkHM6hTzM": 6,  # USDT
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": 9,  # USDe
    },
    "ethereum": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18,  # WETH
        "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": 18,  # ETH (native alias)
    },
    "bsc": {"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": 18},  # WBNB
    "polygon": {"0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": 18},  # WMATIC
    "arbitrum": {"0x82af49447d8a07e3bd95bd0d56f35241523fbab1": 18},  # WETH
    "base": {"0x4200000000000000000000000000000000000006": 18},  # WETH
    "avalanche": {"0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7": 18},  # WAVAX
    "fantom": {"0x21be370d5312f44cb42ce377bc9b8a0cef1a4c83": 18},  # WFTM
    "solana_native_alias": {},
}
