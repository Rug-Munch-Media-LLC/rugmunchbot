# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/deployer_fingerprint.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Deployer Behavioral Fingerprint (C7 v1) — ClickHouse-Powered Deployer Scoring
=============================================================================
Scores every token deployer from our own ClickHouse data (chain-indexer
wallet_memory tables). Goes beyond the numeric reputation in
``own_deployer_reputation`` by assigning a *behavioral bucket*:

  - ``NEW``          — first deployment happened < 7 days ago
  - ``REPEAT_CLEAN`` — >1 deployments, every deployment survived 7+ days
  - ``REPEAT_RUG``   — ANY deployment had zero DEX survival past 7 days
  - ``FARM``         — >3 deployments funded by the same non-CEX wallet
  - ``PROLIFIC``     — >50 deployments with a low rug rate

Data sources (all LOCAL, zero cost):
  - ``wallet_memory.deployer_events``  — deployment stats + per-token history
  - ``wallet_memory.dex_events``       — DEX swap activity → survival/lifespan
  - ``wallet_memory.evm_transfers``    — funding analysis (first funder)
  - ``wallet_memory.wallet_labels``    — funder CEX / label classification

Caching:
  - Redis key ``fingerprint:{chain}:{deployer}`` with 1h TTL (async, graceful
    fallback when Redis is unavailable).
  - In-process dict cache (10 min) mirrors the other own-data engines.

Usage:
    from rugmunchbot._vendor.scanner_engines.deployer_fingerprint import fingerprint_deployer

    result = await fingerprint_deployer("0x...", "ethereum")

Notes:
  - ``pair_created_ts`` is proxied by the earliest DEX swap timestamp for the
    token because chain-indexers persist only ``type = 'swap'`` rows in
    dex_events (pair-created events are not stored as rows).
  - ``funder_address`` is the first wallet that sent the deployer an incoming
    transfer on record. ``evm_transfers`` holds ERC-20 transfers; native ETH
    is indexed only when it carries the native token marker (zero address /
    0xeeee…), otherwise the first ERC-20 funder is used as the proxy.

  [REVIEW] Spec interpretation notes:
  - Risk-bucket precedence is operational, not the display order in the spec:
    PROLIFIC (>50 deployments, success >= 0.8) wins over FARM/REPEAT_RUG so a
    high-volume low-rug-rate deployer is not mislabeled as a rugger; FARM wins
    over REPEAT_RUG because the farm pattern is the more specific signal.
  - A single old (>= 7 days) clean deployment falls into REPEAT_CLEAN; the spec
    buckets only define NEW for young deployers and give no label for an old
    single clean deployment, so REPEAT_CLEAN is the least-bad fit (the count is
    visible in ``total_deployments`` so callers can distinguish).
  - success_rate uses *matured* tokens (deployed >= 7 days ago) as the
    denominator; tokens too young to judge never count against the deployer.

NOTE (degodding 2026-08-14): this module is the public API + a thin re-export
shim. The implementation is split by concern:

  - ``_deployer_fingerprint_helpers.py`` — pure risk/classifier helpers
    (``_assign_risk_bucket``, ``_compute_risk_score``, ``_confidence``,
    ``_classify_cex``) and the result builders (``_unavailable_result`` /
    ``_error_result``).
  - ``_deployer_fingerprint_queries.py`` — the ClickHouse query layer
    (``_query_*``) and query constants (``_NATIVE_TOKEN_MARKERS`` /
    ``_QUERY_TIMEOUT_SECONDS``). The client is resolved lazily through this
    shim so tests patching ``deployer_fingerprint._get_ch_client`` keep
    intercepting end-to-end.
  - ``_deployer_fingerprint_profiles.py`` — funder-profile enrichment
    (``_build_funder_profile``) + launchpad / structural resolution
    (``_resolve_launchpad_context``, ``_resolve_structural_eoa``,
    ``_is_known_launchpad_factory``, ``_launchpad_name``).
  - ``_deployer_fingerprint_compute.py`` — ``_compute_fingerprint`` (the
    query orchestrator + token-outcome / evidence assembly).

Every name is re-exported here so ``deployer_fingerprint.<name>`` references
(tests, scanners, patch targets) keep resolving to this module.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.lite_chain_indexer import (
    # ``as`` alias marks the re-export so mypy strict (implicit_reexport=False)
    # keeps treating ``deployer_fingerprint._get_ch_client`` as exportable —
    # the query submodule resolves the client lazily through this shim.
    _get_ch_client as _get_ch_client,
    _normalize_chain,
)
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
)
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_compute import _compute_fingerprint
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_helpers import (  # noqa: F401
    _CEX_KEYWORDS,
    # Re-exported for tests that reference them as deployer_fingerprint.<name>
    # (see tests/unit/domain/scanner/test_deployer_fingerprint.py).
    RISK_BUCKETS,
    _assign_risk_bucket,
    _classify_cex,
    _compute_risk_score,
    _confidence,
    _error_result,
    _unavailable_result,
)
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_profiles import (
    # Re-exported for tests / callers that reference them as
    # deployer_fingerprint.<name>.
    _build_funder_profile,  # noqa: F401
    _is_known_launchpad_factory,
    _launchpad_name,
    _resolve_launchpad_context,
    _resolve_structural_eoa,
)
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_queries import (  # noqa: F401
    _NATIVE_TOKEN_MARKERS,
    # Re-exported for tests / callers that reference them as
    # deployer_fingerprint.<name>.
    _QUERY_TIMEOUT_SECONDS,
    _query_deployer_stats,
    _query_first_incoming,
    _query_funder,
    _query_funder_label,
    _query_funder_tx_count,
    _query_per_token_deployments,
    _query_same_funder_deployers,
    _query_token_dex_stats,
)

# Legacy module-local names kept so existing unit tests that patch
# ``deployer_fingerprint._redis_get`` / ``deployer_fingerprint._redis_set``
# keep intercepting the shared implementation.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_TTL_SECONDS = 3600  # 1 hour — per spec
REDIS_CACHE_PREFIX = "fingerprint"
_FINGERPRINT_CACHE: dict[str, dict[str, Any]] = {}
_FINGERPRINT_CACHE_TTL = 600  # 10 minutes in-process mirror
_FINGERPRINT_CACHE_MAX = 200


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def fingerprint_deployer(deployer_address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Compute the behavioral fingerprint for a token deployer from ClickHouse.

    Combines deployment stats, DEX survival rate, median token lifespan, and
    funding-source analysis into a risk bucket (NEW / REPEAT_CLEAN / REPEAT_RUG /
    FARM / PROLIFIC) plus a 0-100 risk score (higher = riskier).

    Launchpad awareness:
      If the given deployer is a known launchpad factory (e.g. pump.fun /
      bags.fm), the factory itself is NOT fingerprinted — that would score the
      factory's aggregate history (10M+ deployments) instead of the real
      creator. Instead we resolve the real deployer first (see
      ``launchpad_resolver``) and fingerprint the creator EOA. When resolution
      is unavailable, we return a degraded ``unavailable`` result rather than a
      misleading factory score.

    Args:
        deployer_address: Deployer wallet address (lowercased internally).
            May be a launchpad factory address — resolved to the real creator.
        chain: Chain to analyze (default ``ethereum``). Empty string or
               ``any`` means all chains.

    Returns:
        Dict with the fingerprint::

            {
                "deployer": "0x...",
                "risk_bucket": "NEW" | "REPEAT_CLEAN" | "REPEAT_RUG" | "FARM" | "PROLIFIC",
                "risk_score": 0-100,
                "total_deployments": int,
                "success_rate": float,
                "median_lifespan_hours": float,
                "funder_profile": {...},
                "evidence": [...],
                # launchpad context (only when the input was a factory):
                "launchpad": "pump.fun" | ...,
                "real_deployer": "0x...",       # resolved creator EOA
                "launchpad_resolved": bool,
            }

        Returns an ``unavailable`` result (graceful degradation) when
        ClickHouse is unreachable or the deployer has no own-data history.
    """
    deployer_address = deployer_address.lower().strip()
    chain_norm = _normalize_chain(chain) if chain else ""
    _start = time.monotonic()

    if not deployer_address:
        return _error_result("No deployer address")

    # ── 0. Launchpad factory → real deployer resolution ──
    # Only Solana base58 addresses are case-sensitive; EVM deployers are
    # lowercased above and compared against the (lowercased) registry keys.
    #
    # Universal detection (no factory list required): besides known registry
    # launchpads, ANY deployer that the lite indexer already resolved to a
    # different EOA (deployer_eoa column populated from tx `from` at ingest
    # time) is treated as a factory/contract and traced to the real creator.
    # This catches every launchpad/factory the indexer has ever seen, even
    # when it is not in ``LAUNCHPAD_FACTORIES``.
    launchpad_ctx: dict[str, Any] | None = None
    if _is_known_launchpad_factory(deployer_address, chain_norm):
        launchpad_ctx = await _resolve_launchpad_context(deployer_address, chain_norm)
        if launchpad_ctx is None:
            # Factory is known but we could not trace the real creator —
            # do not fingerprint the factory itself.
            result = _unavailable_result(
                deployer_address,
                chain_norm,
                reason="launchpad factory deployer — real creator could not be resolved",
            )
            result["launchpad"] = _launchpad_name(deployer_address, chain_norm)
            result["launchpad_resolved"] = False
            return result
        deployer_address = launchpad_ctx["real_deployer"]
    else:
        # Structural: indexer-resolved EOA (any factory/contract, registry
        # or not). Only claim a resolution when the EOA differs from the
        # recorded deployer — an EOA deployer with matching eoa is the
        # deployer itself and needs no rewriting.
        structurally_resolved = await _resolve_structural_eoa(deployer_address, chain_norm)
        if structurally_resolved is not None:
            launchpad_ctx = structurally_resolved
            deployer_address = launchpad_ctx["real_deployer"]

    # ── 1. Redis fast cache (1h TTL) ──
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm or 'any'}:{deployer_address}"
    cached = await _redis_get(cache_key)
    if cached is not None:
        logger.debug("deployer_fingerprint_redis_hit", key=cache_key)
        return cached

    # ── 2. In-process mirror cache ──
    if cache_key in _FINGERPRINT_CACHE:
        entry = _FINGERPRINT_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _FINGERPRINT_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. Full ClickHouse computation ──
    try:
        result = await _compute_fingerprint(deployer_address, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("deployer_fingerprint_query_failed: %s", exc)
        result = _unavailable_result(deployer_address, chain_norm)

    # ── Stamp launchpad context (factory → real creator) ──
    if launchpad_ctx is not None:
        result["launchpad"] = launchpad_ctx.get("launchpad")
        result["real_deployer"] = launchpad_ctx.get("real_deployer")
        result["launchpad_resolved"] = True
        result["evidence_tx"] = launchpad_ctx.get("evidence_tx")
        result["launchpad_confidence"] = launchpad_ctx.get("confidence")
        result["deployer"] = deployer_address  # fingerprint is of the real creator
        if launchpad_ctx.get("method") == "deployer_eoa":
            result["note"] = "fingerprint computed for real deployer resolved from deployer_events deployer_eoa"
        else:
            result["note"] = "fingerprint computed for real deployer resolved from launchpad factory"

    # Only cache meaningful results (skip unavailable/error)
    if result.get("result") == "scanned":
        await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
        result["_cached_at"] = time.time()
        _FINGERPRINT_CACHE[cache_key] = result
        if len(_FINGERPRINT_CACHE) > _FINGERPRINT_CACHE_MAX:
            _FINGERPRINT_CACHE.pop(next(iter(_FINGERPRINT_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "deployer_fingerprint_latency",
        ms=round(_elapsed_ms, 1),
        deployer=deployer_address[:10],
        bucket=result.get("risk_bucket"),
        risk=result.get("risk_score"),
        deployments=result.get("total_deployments"),
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _FINGERPRINT_CACHE,
        _FINGERPRINT_CACHE_MAX,
        _FINGERPRINT_CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_FINGERPRINT_CACHE)
