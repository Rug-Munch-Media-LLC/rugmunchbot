# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/funding_analysis.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Clever Wallet Analysis — Exchange Funding, Freshness, Holder Overlap
=====================================================================
RugMunchScan metrics other security firms don't bother with.

Three metrics, all computed from our own ClickHouse data:

  1. ``analyze_funding_sources`` — Exchange-Funded Wallet Percentage.
     What % of a token's early holders were funded by CEX wallets?
     High % = potential insider/coordinated launch. Also classifies
     fresh-wallet-funded and mixer-funded holders.

  2. ``compute_wallet_freshness`` — Wallet Freshness Score.
     How "fresh" is a wallet (age, tx count, first funder)? New wallets
     buying a token = potential farm.

  3. ``compute_holder_overlap`` — Holder Overlap Analysis.
     How many holders of token A also hold token B? High overlap (>30%)
     with a known scam token = coordinated farm group red flag.

  4. ``compute_wallet_metrics`` — Token-Level Wallet Freshness Metrics.
     For ANY token: % of holders <30 days old, % funded by CEX wallets,
     % whose first-ever on-chain tx is this token (completely fresh).

Data Sources:
  Primary: wallet_memory.evm_transfers (funding trace, holder sets)
  Primary: wallet_memory.solana_spl_balance_changes (Solana holder sets)
  Primary: wallet_memory.wallet_labels (39.4M entity labels)
  Primary: wallet_memory.deployer_history (known scam tokens, is_scam_related)
  Static:  known_entities.EXCHANGE_WALLETS / EVM_KNOWN_CONTRACTS (CEX/mixer)

Algorithm (Metric 1):
  1. Resolve top N holders (passed in, or from evm_transfers /
     solana_spl_balance_changes net balances).
  2. For each holder, find their FIRST incoming transfer on the chain
     (argMin(from_address, block_timestamp) across all tokens) — the
     wallet's original funding source.
  3. Classify the funder via wallet_labels + known_entities:
     CEX → exchange_funded; mixer → mixer_funded; <=10 txs → fresh_wallet.
  4. Cross-reference funders — funders that funded many holders surface in
     ``top_funders``.
  5. Percentages + risk assessment (exchange_funded_pct > 50 → HIGH).

Every ClickHouse query is bounded by ``max_execution_time`` (time-boxed —
see Cross-Cutting Principles). All caches are graceful on failure.

Usage:
    from rugmunchbot._vendor.scanner_engines.funding_analysis import (
        analyze_funding_sources,
        compute_holder_overlap,
        compute_wallet_freshness,
        compute_wallet_metrics,
    )

    funding = await analyze_funding_sources("0x...", "ethereum", holders=[...])
    fresh   = await compute_wallet_freshness("0x...", "ethereum")
    overlap = await compute_holder_overlap("0xA...", "0xB...", "ethereum")
    metrics = await compute_wallet_metrics("0x...", "ethereum")

NOTE (degodding 2026-08-08): the pure-logic section (funder classification,
freshness scoring, risk assessment, top-funder / scam-overlap aggregation)
was extracted into ``engines/_funding_helpers.py`` (wave 5).

NOTE (degodding 2026-08-14): this module is now the public API + a thin
re-export shim. The fetch / storage layer lives in ``engines/_io.py``
(in-process cache, Redis wrappers, query/analysis limits, the ``_compute_*``
ClickHouse orchestrators, ``get_cache_stats`` / ``clear_cache``) and the
pure scoring math lives in ``engines/_scoring.py``
(``_aggregate_wallet_metrics``, ``_FRESH_WALLET_AGE_DAYS``). Every name from
the co-located modules is re-exported here so existing ``fa.*`` references
(tests, scanners) keep resolving to this module.
"""

from __future__ import annotations

import time
from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.lite_chain_indexer import _normalize_chain
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    _query_batch_labels,  # noqa: F401
    redis_get,  # noqa: F401
    redis_set,  # noqa: F401
    run_sync,
    to_epoch,  # noqa: F401
    to_iso,  # noqa: F401
)
from rugmunchbot._vendor.scanner_engines._funding_helpers import (
    # Re-exported for tests that reference them as fa.<name> (see
    # tests/unit/domain/scanner/test_funding_analysis.py).
    _CEX_FUNDED_PTS,  # noqa: F401
    _FRESH_CLEAN_MIN,  # noqa: F401
    _FRESH_SUSPICIOUS_MIN,  # noqa: F401
    _OVERLAP_SCAM_PCT,  # noqa: F401
    _RISK_DESCRIPTIONS,  # noqa: F401
    _aggregate_scam_overlap,
    _assess_funding_risk,  # noqa: F401
    _build_top_funders,  # noqa: F401
    _classify_freshness,  # noqa: F401
    _classify_funder,  # noqa: F401
    _compute_freshness_score,  # noqa: F401
)
from rugmunchbot._vendor.scanner_engines._funding_queries import (
    _merge_first_tx_rows,  # noqa: F401
    _query_first_funders,  # noqa: F401
    _query_funder_tx_counts,  # noqa: F401
    _query_holder_first_tx,  # noqa: F401
    _query_holder_first_tx_in,  # noqa: F401
    _query_holder_first_tx_out,  # noqa: F401
    _query_holder_set,  # noqa: F401
    _query_known_scam_tokens,
    _query_token_coverage_start,  # noqa: F401
    _query_token_scam_status,  # noqa: F401
    _query_top_holders,  # noqa: F401
    _query_wallet_first_funder,  # noqa: F401
    _query_wallet_stats,  # noqa: F401
)
from rugmunchbot._vendor.scanner_engines._funding_results import (
    _error_result,
    _freshness_unavailable,
    _overlap_error,
    _overlap_unavailable,
    _scam_overlap_error,
    _unavailable_result,
    _wallet_metrics_error,
    _wallet_metrics_unavailable,
)
from rugmunchbot._vendor.scanner_engines._io import (
    _CACHE,
    _CACHE_MAX,
    _CACHE_TTL,
    _DEAD_ADDR,  # noqa: F401
    _MAX_HOLDERS,  # noqa: F401
    _MAX_SCAM_TOKENS,
    _QUERY_TIMEOUT_SECONDS,  # noqa: F401
    _ZERO_ADDR,  # noqa: F401
    REDIS_CACHE_PREFIX,
    REDIS_TTL_SECONDS,
    _compute_freshness,
    _compute_funding,
    _compute_overlap,
    _compute_wallet_metrics,
    _redis_get,
    _redis_set,
    clear_cache,
    get_cache_stats,
)
from rugmunchbot._vendor.scanner_engines._scoring import (
    _FRESH_WALLET_AGE_DAYS,  # noqa: F401
    _aggregate_wallet_metrics,  # noqa: F401
)

__all__ = [
    "REDIS_CACHE_PREFIX",
    "REDIS_TTL_SECONDS",
    "analyze_funding_sources",
    "analyze_scam_overlap",
    "clear_cache",
    "compute_holder_overlap",
    "compute_wallet_freshness",
    "compute_wallet_metrics",
    "get_cache_stats",
]

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — Metric 1: Exchange-Funded Wallet Percentage
# ═══════════════════════════════════════════════════════════════════════════


async def analyze_funding_sources(
    token_address: str,
    chain: str,
    holders: list[str] | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Trace the first funding source of a token's top holders.

    Args:
        token_address: Token contract address.
        chain: Chain name (ethereum, bsc, arbitrum, solana, ...).
        holders: Top-N holder addresses. When omitted, resolved from
            ``evm_transfers`` (EVM) or ``solana_spl_balance_changes``
            (Solana) net balances.
        force_fresh: Bypass all caches.

    Returns:
        {
            "check": "funding_sources",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_address": "0x...",
            "chain": "ethereum",
            "holders_analyzed": 12,
            "exchange_funded_pct": 45.2,
            "fresh_wallet_pct": 22.1,
            "mixer_funded_pct": 1.3,
            "unknown_pct": 31.4,
            "top_funders": [
                {"address": "0x...", "label": "Binance Hot Wallet", "funded_holders": 12}
            ],
            "holder_details": [
                {"holder": "0x...", "funder": "0x...", "funding_type": "exchange_funded"}
            ],
            "risk_assessment": "HIGH",
            "note": "...",
        }
    """
    token_address = token_address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_address or not chain_norm:
        return _error_result(token_address, chain_norm, "token_address and chain are required")

    # ── 1. Redis fast cache ──
    cache_key = f"{REDIS_CACHE_PREFIX}:funding:{chain_norm}:{token_address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("funding_analysis_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)

    # ── 2. In-process mirror cache ──
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. Full ClickHouse computation ──
    try:
        result = await _compute_funding(token_address, chain_norm, holders)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("funding_analysis_query_failed: %s", exc)
        result = _unavailable_result(token_address, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "funding_analysis_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        holders=result.get("holders_analyzed", 0),
        exchange_pct=result.get("exchange_funded_pct", 0.0),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — Metric 2: Wallet Freshness Score
# ═══════════════════════════════════════════════════════════════════════════


async def compute_wallet_freshness(
    address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Score how "fresh" (new / farm-like) a wallet is.

    Args:
        address: Wallet address to analyze.
        chain: Chain name (ethereum, bsc, arbitrum, solana, ...).
        force_fresh: Bypass all caches.

    Returns:
        {
            "check": "wallet_freshness",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "address": "0x...",
            "chain": "ethereum",
            "age_days": 3,
            "total_txs": 12,
            "funded_by_cex": True,
            "first_funder": "0x...",
            "freshness_score": 85,
            "classification": "FRESH_SUSPICIOUS",
            "note": "...",
        }

    Freshness bands (score 0-100, higher = fresher/more suspicious):
      - >= 70  → FRESH_SUSPICIOUS (new wallet with CEX funding / few txs)
      - >= 40  → FRESH_CLEAN (new-ish but no strong red flags)
      - <  40  → ESTABLISHED (old, active wallet)
    """
    address = address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not address or not chain_norm:
        return {
            "check": "wallet_freshness",
            "result": "error",
            "error": "address and chain are required",
            "address": address,
            "chain": chain_norm,
            "age_days": 0,
            "total_txs": 0,
            "funded_by_cex": False,
            "first_funder": None,
            "freshness_score": 0,
            "classification": "ESTABLISHED",
        }

    cache_key = f"{REDIS_CACHE_PREFIX}:fresh:{chain_norm}:{address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("wallet_freshness_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        result = await _compute_freshness(address, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("wallet_freshness_query_failed: %s", exc)
        result = _freshness_unavailable(address, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "wallet_freshness_latency",
        ms=round(_elapsed_ms, 1),
        wallet=address[:10],
        chain=chain_norm,
        score=result.get("freshness_score", 0),
        classification=result.get("classification", "ESTABLISHED"),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — Metric 3: Holder Overlap Analysis
# ═══════════════════════════════════════════════════════════════════════════


async def compute_holder_overlap(
    token_a: str,
    token_b: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Cross-reference the holder sets of two tokens.

    Args:
        token_a: First token (the scanned token).
        token_b: Second token (often a known scam token).
        chain: Chain name.
        force_fresh: Bypass all caches.

    Returns:
        {
            "check": "holder_overlap",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_a": "0xA...",
            "token_b": "0xB...",
            "chain": "ethereum",
            "holders_a": 120,
            "holders_b": 80,
            "common_holders": 45,
            "overlap_pct": 37.5,       # |A ∩ B| / |A| * 100
            "token_b_is_scam": True,
            "risk_assessment": "HIGH",  # >30% overlap with known scam
            "note": "...",
        }

    High overlap (>30%) with a known scam token = red flag
    (coordinated farm group).
    """
    token_a = token_a.lower().strip()
    token_b = token_b.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_a or not token_b or not chain_norm:
        return _overlap_error(token_a, token_b, chain_norm, "token_a, token_b and chain are required")

    cache_key = f"{REDIS_CACHE_PREFIX}:overlap:{chain_norm}:{token_a}:{token_b}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("holder_overlap_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        result = await _compute_overlap(token_a, token_b, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("holder_overlap_query_failed: %s", exc)
        result = _overlap_unavailable(token_a, token_b, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "holder_overlap_latency",
        ms=round(_elapsed_ms, 1),
        token_a=token_a[:12],
        token_b=token_b[:12],
        chain=chain_norm,
        overlap_pct=result.get("overlap_pct", 0.0),
    )
    return result


async def analyze_scam_overlap(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Cross-reference a token's holders against known scam tokens.

    Scanner-level helper: resolves up to ``_MAX_SCAM_TOKENS`` known scam
    tokens on the chain (from ``deployer_history`` where
    ``is_scam_related = 1``) and reports the worst holder overlap.

    Returns:
        {
            "check": "scam_overlap",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_address": "0x...",
            "chain": "ethereum",
            "scam_tokens_checked": 3,
            "max_overlap_pct": 37.5,
            "worst_token": "0xB...",
            "worst_token_is_scam": True,
            "risk_assessment": "HIGH",
            "comparisons": [ ... per-token overlap result ... ],
        }
    """
    token_address = token_address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_address or not chain_norm:
        return _scam_overlap_error(token_address, chain_norm, "token_address and chain are required")

    cache_key = f"{REDIS_CACHE_PREFIX}:scam_overlap:{chain_norm}:{token_address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("scam_overlap_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        scam_tokens = await run_sync(_query_known_scam_tokens, chain_norm, _MAX_SCAM_TOKENS)
        scam_tokens = [t for t in scam_tokens if t and t.lower() != token_address]
        if not scam_tokens:
            result = {
                "check": "scam_overlap",
                "result": "unavailable",
                "source": "own",
                "token_address": token_address,
                "chain": chain_norm,
                "scam_tokens_checked": 0,
                "max_overlap_pct": 0.0,
                "worst_token": None,
                "worst_token_is_scam": False,
                "risk_assessment": "LOW",
                "comparisons": [],
                "note": "no known scam tokens on chain for cross-reference",
            }
        else:
            comparisons = []
            for scam_token in scam_tokens:
                overlap = await _compute_overlap(token_address, scam_token, chain_norm)
                if overlap.get("result") == "scanned":
                    comparisons.append(overlap)
            result = _aggregate_scam_overlap(token_address, chain_norm, comparisons)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("scam_overlap_query_failed: %s", exc)
        result = _scam_overlap_error(token_address, chain_norm, str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "scam_overlap_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        checked=result.get("scam_tokens_checked", 0),
        max_overlap=result.get("max_overlap_pct", 0.0),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API — Metric 4: Token-Level Wallet Freshness Metrics
# ═══════════════════════════════════════════════════════════════════════════


async def compute_wallet_metrics(
    token_address: str,
    chain: str,
    holders: list[str] | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Compute token-level wallet freshness metrics for ANY token.

    Three metrics over the token's top holders (from ``evm_transfers`` net
    balances):

      - ``fresh_wallet_pct`` — % of holders whose wallet is <30 days old
        (first-seen on the chain is recent — farm-like concentration).
      - ``cex_funded_pct`` — % of holders whose FIRST funder is a CEX wallet
        (Binance, Coinbase, Kraken, Bybit, OKX, ... — institutional backing).
      - ``first_tx_pct`` — % of holders whose first-ever on-chain tx is this
        token (completely fresh wallets — no prior token history).

    Uses ``wallet_memory.evm_transfers`` for wallet age + funder lookups
    (the same tables as the other funding metrics). Best-effort: returns an
    ``unavailable`` result when ClickHouse has no holder/age data.

    Args:
        token_address: Token contract address.
        chain: Chain name (ethereum, bsc, arbitrum, ...).
        holders: Top-N holder addresses. When omitted, resolved from
            ``evm_transfers`` net balances.
        force_fresh: Bypass all caches.

    Returns:
        {
            "check": "wallet_metrics",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_address": "0x...",
            "chain": "ethereum",
            "holders_analyzed": 25,
            "fresh_wallet_pct": 12.3,
            "cex_funded_pct": 45.6,
            "first_tx_pct": 8.0,
            "fresh_wallet_count": 3,
            "cex_funded_count": 11,
            "first_tx_count": 2,
            "holder_details": [...],
            "note": "...",
        }
    """
    token_address = token_address.lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_address or not chain_norm:
        return _wallet_metrics_error(token_address, chain_norm, "token_address and chain are required")

    cache_key = f"{REDIS_CACHE_PREFIX}:metrics:{chain_norm}:{token_address}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("wallet_metrics_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)
    if not force_fresh and cache_key in _CACHE:
        entry = _CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        result = await _compute_wallet_metrics(token_address, chain_norm, holders)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("wallet_metrics_query_failed: %s", exc)
        result = _wallet_metrics_unavailable(token_address, chain_norm, reason=str(exc)[:120])

    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _CACHE[cache_key] = result
        if len(_CACHE) > _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "wallet_metrics_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        holders=result.get("holders_analyzed", 0),
        fresh_pct=result.get("fresh_wallet_pct", 0.0),
        cex_pct=result.get("cex_funded_pct", 0.0),
    )
    return result
