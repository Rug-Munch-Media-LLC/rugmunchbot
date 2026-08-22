"""trend_algo constants — scoring formula, chain aliases, route limits.

Extracted verbatim from the former single-module ``tools/trend_algo.py``.
"""

from __future__ import annotations

from typing import Any

# DexScreener token-profiles is NOT under /latest/dex — absolute URL.
DEXSCREENER_TOKEN_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"  # noqa: S105 — public API URL (no secret)

# Backend route limits (validated by probe: scanner/recent le=100,
# market/trending le=50).
_SCANNER_RECENT_LIMIT = 100
_TRENDING_LIMIT = 50
_LAUNCHES_LIMIT = 80

# ── Transparent scoring constants (mirrored in FORMULA payload) ───────────
_VOL_REF_USD = 1_000_000.0
_LIQ_REF_USD = 250_000.0
_TRADES_REF = 2_000.0
_MOMENTUM_REF_PCT = 100.0  # |24h %| reference — a 100% move (either way)
_AGE_DECAY_HOURS = 72.0
_W_VOL = 0.25
_W_LIQ = 0.15
_W_GROWTH = 0.15
_W_MOMENTUM = 0.20
_W_AGE = 0.25
_RISK_FLOOR = 50.0
_RISK_MAX_PENALTY = 0.60

_FORMULA: dict[str, Any] = {
    "score": "100 * (w_vol*S_vol + w_liq*S_liq + w_growth*S_growth + w_momentum*S_momentum + w_age*S_age) * (1 - risk_penalty)",
    "weights": {
        "volume_24h": _W_VOL,
        "liquidity": _W_LIQ,
        "holder_growth": _W_GROWTH,
        "momentum_24h": _W_MOMENTUM,
        "age": _W_AGE,
    },
    "S_vol": "1 - exp(-volume_24h / 1_000_000)",
    "S_liq": "1 - exp(-liquidity_usd / 250_000)",
    "S_growth": "0.5*clamp01((buy_ratio-0.40)/0.60) + 0.5*(1 - exp(-(buys+sells)/2_000))",
    "S_momentum": "1 - exp(-abs(price_change_24h) / 100)  # |%| mover, up AND down",
    "S_age": "1 - min(age_hours/72, 1)  (unknown -> 0.5)",
    "risk_penalty": "1.0 if honeypot/rug-pull else clamp01((risk_score-50)/50)*0.60 (0 if unscanned)",
    "note": (
        "holder_growth is proxied by h24 buy pressure + trade activity; "
        "momentum_24h rewards the biggest |%| movers in both directions "
        "(green pumps AND red rugs) — fresh launches and movers outrank "
        "established infra tokens that nobody trades."
    ),
}

# DexScreener chain id normalisation (aliases -> canonical DexScreener id)
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
    "arbitrum-one": "arbitrum",
    "base": "base",
    "polygon": "polygon",
    "matic": "polygon",
    "polygon-pos": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avax": "avalanche",
    "avalanche": "avalanche",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "robinhood": "robinhood",
    "ton": "ton",
    "tron": "tron",
    "trx": "tron",
    "zksync": "zksync",
    "polygon_zkevm": "polygon_zkevm",
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

_METRIC_FETCH_CONCURRENCY = 10
_MAX_CANDIDATES = 60
_METRIC_FETCH_DEADLINE = 10.0  # seconds; partial results still return
