"""trend_by_chain tool — per-chain trending with OUR transparent scoring formula.

Modularized from the former single-module ``tools/trend_algo.py`` (2026-08-22).
This ``__init__`` is a pure re-export shim: every public name and signature
is unchanged, and callers keep importing ``tools.trend_algo`` exactly as
before.

The tool computes trending tokens PER CHAIN using a transparent, documented
scoring formula over OUR data where available, with DexScreener (and
optionally Birdeye) as the metric fallback.

Data cascade (own data first, external fallback):

  1. Own-data candidates
     - backend ``/api/v1/scanner/recent``          — our scanner activity
       (real risk scores, honeypot / rug-pull flags, chain, real symbol).
     - backend ``/api/v1/market/trending``         — our fused trending feed.
     - backend ``/api/v1/launches/recent``         — ClickHouse
       ``deployer_events`` (our own chain indexer) when rows exist.
  2. DexScreener ``/token-profiles/latest/v1``     — fresh launches per chain
     (real addresses + chain; used only as candidate seeds).
  3. Per-token metrics — DexScreener ``/latest/dex/search?q={address}``
     (24h volume, liquidity USD, buy/sell txs h24, pair age). This is the
     fallback metric layer (free, no key).
  4. Optional Birdeye ``token_overview`` holder counts (only when a
     ``BIRDEYE_API_KEY`` is present; silently degrades to None on
     rate-limit/errors — never blocks).

SCORING FORMULA (returned in the payload under ``scoring.formula`` so it is
fully auditable):

    score = 100 * ( w_vol * S_vol
                  + w_liq * S_liq
                  + w_growth * S_growth
                  + w_momentum * S_momentum
                  + w_age * S_age ) * (1 - risk_penalty)

Age and |mover| weights are deliberately high: fresh launches and the biggest
% movers are what degens trade. Only REAL symbols / addresses are ever
returned; candidates without a resolvable pair or real symbol are dropped.

Layout:
  * ``_constants.py``  — scoring constants, _FORMULA, chain aliases, limits
  * ``_normalize.py``  — chain normalisation + float/int coercion helpers
  * ``_candidates.py`` — own-data + DexScreener candidate collectors
  * ``_metrics.py``    — DexScreener pair metrics + optional Birdeye holders
  * ``_scoring.py``    — trend score + risk-first sketch score (pure)
  * ``_tool.py``       — trend_by_chain handler + register_tool
"""

from __future__ import annotations

from rugmunchbot.tools.trend_algo._candidates import (
    _dex_profile_candidates as _dex_profile_candidates,
    _launch_candidates as _launch_candidates,
    _scanner_candidates as _scanner_candidates,
    _trending_candidates as _trending_candidates,
)
from rugmunchbot.tools.trend_algo._constants import (
    _AGE_DECAY_HOURS as _AGE_DECAY_HOURS,
    _CHAIN_ALIASES as _CHAIN_ALIASES,
    _FORMULA as _FORMULA,
    _LAUNCHES_LIMIT as _LAUNCHES_LIMIT,
    _LIQ_REF_USD as _LIQ_REF_USD,
    _MAX_CANDIDATES as _MAX_CANDIDATES,
    _METRIC_FETCH_CONCURRENCY as _METRIC_FETCH_CONCURRENCY,
    _METRIC_FETCH_DEADLINE as _METRIC_FETCH_DEADLINE,
    _MOMENTUM_REF_PCT as _MOMENTUM_REF_PCT,
    _RISK_FLOOR as _RISK_FLOOR,
    _RISK_MAX_PENALTY as _RISK_MAX_PENALTY,
    _SCANNER_RECENT_LIMIT as _SCANNER_RECENT_LIMIT,
    _TRADES_REF as _TRADES_REF,
    _TRENDING_LIMIT as _TRENDING_LIMIT,
    _VOL_REF_USD as _VOL_REF_USD,
    _W_AGE as _W_AGE,
    _W_GROWTH as _W_GROWTH,
    _W_LIQ as _W_LIQ,
    _W_MOMENTUM as _W_MOMENTUM,
    _W_VOL as _W_VOL,
    DEXSCREENER_TOKEN_PROFILES as DEXSCREENER_TOKEN_PROFILES,
)
from rugmunchbot.tools.trend_algo._metrics import (
    _birdeye_holders as _birdeye_holders,
    _fetch_pair_metrics as _fetch_pair_metrics,
    _time_ms_now as _time_ms_now,
)
from rugmunchbot.tools.trend_algo._normalize import (
    _clamp01 as _clamp01,
    _exp_norm as _exp_norm,
    _normalize_chain as _normalize_chain,
    _to_float as _to_float,
    _to_int as _to_int,
)
from rugmunchbot.tools.trend_algo._scoring import (
    _score_token as _score_token,
    _sketch_score as _sketch_score,
)
from rugmunchbot.tools.trend_algo._tool import (
    _iso_now as _iso_now,
    register_tool as register_tool,
    trend_by_chain as trend_by_chain,
)

__all__ = [
    "register_tool",
    "trend_by_chain",
]
