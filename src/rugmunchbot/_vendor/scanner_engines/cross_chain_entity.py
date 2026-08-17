# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/cross_chain_entity.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
C4 — Cross-Chain Entity Resolution (v2)
=========================================
Phase 5 RugMunchScan capability — link the same actor across chains
(``docs/scan-capabilities-spec.md`` section C4, P1).

v2 implements all three evidence checks from the spec:

  1. **Same deployer** — ``deployer_events`` rows keyed by deployer address.
     When the queried address deployed tokens on 2+ chains, emit links with
     the first deploy tx hash per chain as evidence.
  2. **Same funder** — the first incoming transfer to the address on the
     source chain identifies a funder (``evm_transfers``); addresses on
     target chains funded by that same funder are candidate links, with the
     funding tx hash as evidence.
  3. **Behavioral fingerprint similarity** — v2 addition. When the deployer
     check finds a candidate, compare the wallet's behavioral fingerprint
     (``behavioral_fingerprint`` engine: tx frequency, DEX interaction,
     temporal patterns, value distribution) on the source chain against the
     candidate chain. A similarity >= ``BEHAVIORAL_MIN_SIMILARITY`` emits a
     ``behavioral`` evidence link that *confirms* the deployer candidate.

Entity classification (bridge vs launderer, spec C4 step 3) is a v2 item.
v2 emits ``entity_type`` = ``"multi_chain_actor"`` when links exist and
``"unknown"`` otherwise, plus a heuristic ``risk`` (low/medium/high).

Confidence scoring follows ``docs/cross-chain-entity-tracking.md`` §6:
  - Same EVM deployer (all chains EVM): 0.95
  - Mixed-chain deployer (EVM + non-EVM): 0.80
  - Non-EVM deployer only: 0.60
  - Same funder (fund-flow trace): 0.60
  - Behavioral fingerprint: 0.70 (weighted by fingerprint similarity)

Every ClickHouse query is bounded by ``max_execution_time`` (time-boxed — see
Cross-Cutting Principles). Links carry evidence (deploy tx hash / funder tx
hash / behavioral similarity).

Usage:
    from rugmunchbot._vendor.scanner_engines.cross_chain_entity import (
        resolve_cross_chain_entity,
    )

    result = await resolve_cross_chain_entity(
        "0xabc...", "ethereum", target_chains=["bsc", "arbitrum", "solana"]
    )
    # result = {
    #     "result": "scanned" | "unavailable" | "error",
    #     "address": "0xabc...",
    #     "source_chain": "ethereum",
    #     "addresses": [
    #         {"chain", "address", "evidence_type", "confidence", "evidence"},
    #         ...
    #     ],
    #     "entity_type": "unknown" | "multi_chain_actor",
    #     "risk": "low" | "medium" | "high",
    #     "behavioral_fingerprint": { ... } | None,
    # }

Data Sources:
  Primary: wallet_memory.deployer_events (deployer + deploy tx evidence)
  Primary: wallet_memory.evm_transfers (first funder + funder tx evidence)
  Primary: wallet_memory.evm_transfers + dex_events (behavioral fingerprint)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from redis.exceptions import RedisError

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.lite_chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    error_result,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
    to_iso,
    unavailable_result,
)
from rugmunchbot._vendor.scanner_engines.behavioral_fingerprint import (
    BEHAVIORAL_MIN_SIMILARITY,
    CONFIDENCE_BEHAVIORAL,
    compare_fingerprints,
    compute_behavioral_fingerprint,
    matched_features,
)

# Legacy module-local names kept for consistency with the other engines.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
REDIS_CACHE_PREFIX = "cross_chain_entity"
REDIS_TTL_SECONDS = 600  # 10 minutes — entity_cache TTL per cross-chain doc
_ENTITY_CACHE: dict[str, dict[str, Any]] = {}
_ENTITY_CACHE_TTL = 120  # 2 minutes in-process mirror
_ENTITY_CACHE_MAX = 200

# ── Query / analysis limits (time-boxing) ─────────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10
_MAX_FUNDER_CANDIDATES = 10  # same-funder candidate links per resolution
_MAX_BEHAVIORAL_CONFIRMATIONS = 5  # behavioral confirmations per resolution

# ── Confidence thresholds (docs/cross-chain-entity-tracking.md §6) ────────
_CONFIDENCE_DEPLOYER_EVM = 0.95
_CONFIDENCE_DEPLOYER_MIXED = 0.80
_CONFIDENCE_DEPLOYER_NON_EVM = 0.60
_CONFIDENCE_SAME_FUNDER = 0.60

# ── EVM chains share the same 0x 20-byte address format ───────────────────
_EVM_CHAINS = {
    "ethereum",
    "bsc",
    "arbitrum",
    "base",
    "polygon",
    "optimism",
    "avalanche",
    "fantom",
    "gnosis",
}

_ZERO_ADDR = "0x0000000000000000000000000000000000000000"
_DEAD_ADDR = "0x000000000000000000000000000000000000dead"

# ── Small helpers ─────────────────────────────────────────────────────────


def _norm_addr(address: str) -> str:
    """Normalize an address: lowercase EVM 0x form, leave base58 (Solana/Tron) as-is."""
    address = (address or "").strip()
    if address.startswith("0x"):
        return address.lower()
    return address


def _deployer_confidence(chains: list[str]) -> float:
    """Confidence that the same deployer address across chains = same entity."""
    evm = [c for c in chains if c in _EVM_CHAINS]
    non_evm = [c for c in chains if c not in _EVM_CHAINS]
    if non_evm and evm:
        return _CONFIDENCE_DEPLOYER_MIXED
    if non_evm:
        return _CONFIDENCE_DEPLOYER_NON_EVM
    return _CONFIDENCE_DEPLOYER_EVM


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (all sync, bounded by max_execution_time)
# ═══════════════════════════════════════════════════════════════════════════


def _query_deployer_chains(deployer_address: str) -> list[dict[str, Any]] | None:
    """Chains where the given deployer deployed tokens (deploy tx evidence).

    Returns one row per chain with ``tokens_deployed`` and the first deploy
    tx hash (evidence). ``None`` on client/query failure, empty list if the
    address is not a tracked deployer.
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                chain,
                uniqExact(token_address) AS tokens_deployed,
                argMin(tx_hash, block_timestamp) AS first_deploy_tx,
                min(block_timestamp) AS first_deployed_ts
            FROM wallet_memory.deployer_events
            WHERE deployer = %(deployer)s
            GROUP BY chain
            """,
            {"deployer": deployer_address},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("cross_chain_deployer_chains_failed: %s", exc)
        return None

    results: list[dict[str, Any]] = []
    for row in rows or []:
        results.append(
            {
                "chain": str(row[0] or "").lower(),
                "tokens_deployed": int(row[1] or 0),
                "evidence": str(row[2] or ""),
                "first_deployed_ts": to_iso(row[3]),
            }
        )
    return results


def _query_first_funder(address: str, chain: str) -> dict[str, Any] | None:
    """First wallet that funded the address (evm_transfers first incoming).

    Returns ``{funder, first_tx, first_ts, first_token}`` or ``None`` when
    the address has no incoming transfer on the source chain.
    """
    client = _get_ch_client()
    if client is None:
        return None

    try:
        rows = client.execute(
            """
            SELECT
                from_address,
                argMin(tx_hash, block_timestamp) AS first_tx,
                min(block_timestamp) AS first_ts,
                argMin(token_address, block_timestamp) AS first_token
            FROM wallet_memory.evm_transfers
            WHERE to_address = %(address)s
              AND chain = %(chain)s
              AND from_address NOT IN ('', %(zero)s, %(dead)s)
            GROUP BY from_address
            ORDER BY first_ts ASC
            LIMIT 1
            """,
            {"address": address, "chain": chain, "zero": _ZERO_ADDR, "dead": _DEAD_ADDR},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("cross_chain_first_funder_failed: %s", exc)
        return None

    if not rows:
        return None
    row = rows[0]
    return {
        "funder": str(row[0] or "").lower(),
        "evidence": str(row[1] or ""),
        "first_ts": to_iso(row[2]),
        "first_token": str(row[3] or ""),
    }


def _query_same_funder_candidates(
    funder: str,
    exclude_chain: str,
    target_chains: list[str] | None,
) -> list[dict[str, Any]] | None:
    """Addresses on other chains funded by the same funder (funder tx evidence).

    Bounded by ``_MAX_FUNDER_CANDIDATES``; rows ordered by first funding
    timestamp. v1 approximation: any address the funder directly funded on a
    target chain is a candidate link — exact "first-funder" verification per
    candidate is a v2 refinement.
    """
    client = _get_ch_client()
    if client is None:
        return None

    where_parts = ["from_address = %(funder)s", "chain != %(exclude)s"]
    params: dict[str, Any] = {
        "funder": funder,
        "exclude": exclude_chain,
        "limit": _MAX_FUNDER_CANDIDATES,
    }
    if target_chains:
        where_parts.append("chain IN %(chains)s")
        params["chains"] = tuple(target_chains)
    where_clause = " AND ".join(where_parts)

    try:
        rows = client.execute(
            f"""
            SELECT
                chain,
                to_address,
                argMin(tx_hash, block_timestamp) AS funder_tx,
                min(block_timestamp) AS first_ts
            FROM wallet_memory.evm_transfers
            WHERE {where_clause}
              AND to_address NOT IN ('', %(zero)s, %(dead)s)
            GROUP BY chain, to_address
            ORDER BY first_ts ASC
            LIMIT %(limit)s
            """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
            {**params, "zero": _ZERO_ADDR, "dead": _DEAD_ADDR},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError, CHDriverError) as exc:
        logger.debug("cross_chain_same_funder_candidates_failed: %s", exc)
        return None

    results: list[dict[str, Any]] = []
    for row in rows or []:
        results.append(
            {
                "chain": str(row[0] or "").lower(),
                "address": _norm_addr(str(row[1] or "")),
                "evidence": str(row[2] or ""),
                "first_ts": to_iso(row[3]),
            }
        )
    return results


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def resolve_cross_chain_entity(
    address: str,
    source_chain: str,
    target_chains: list[str] | None = None,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """C4 v2: cross-chain entity resolution.

    Links the given wallet to addresses on other chains via:
      1. Same deployer — the address deployed tokens on 2+ chains
         (``deployer_events``, deploy tx hash evidence).
      2. Same funder — addresses on target chains funded by the same first
         funder (``evm_transfers``, funding tx hash evidence).
      3. Behavioral fingerprint similarity — when the deployer check finds a
         candidate, compute the behavioral fingerprint on the source and
         candidate chains and emit a ``behavioral`` confirmation link when the
         cosine similarity meets ``BEHAVIORAL_MIN_SIMILARITY``.

    Args:
        address: Wallet/deployer address on the source chain.
        source_chain: Chain the address lives on (ethereum, bsc, solana, ...).
        target_chains: Optional chain whitelist for candidate links. Defaults
            to all chains present in our data.
        force_fresh: Bypass Redis + in-process caches.

    Returns:
        {
            "result": "scanned" | "unavailable" | "error",
            "address": <normalized address>,
            "source_chain": <normalized source chain>,
            "addresses": [
                {"chain", "address", "evidence_type", "confidence", "evidence"},
            ],
            "entity_type": "unknown" | "multi_chain_actor",
            "risk": "low" | "medium" | "high",
            "behavioral_fingerprint": { ... } | None,
        }
    """
    addr = _norm_addr(address)
    chain_norm = _normalize_chain(source_chain)
    _start = time.monotonic()

    if not addr or not chain_norm:
        return error_result("address and source_chain are required")

    targets = [_normalize_chain(c) for c in (target_chains or []) if c]
    targets = [c for c in targets if c]

    # ── 1. Redis fast cache ──
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm}:{addr}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            logger.debug("cross_chain_entity_redis_hit", key=cache_key)
            return cast("dict[str, Any]", cached)

    # ── 2. In-process mirror cache ──
    if not force_fresh and cache_key in _ENTITY_CACHE:
        entry = _ENTITY_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _ENTITY_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    # ── 3. ClickHouse evidence gathering (parallel) ──
    if _get_ch_client() is None:
        return unavailable_result("clickhouse_unavailable")
    deployer_task = asyncio.create_task(run_sync(_query_deployer_chains, addr))
    funder_task = asyncio.create_task(run_sync(_query_first_funder, addr, chain_norm))
    deployer_rows = await deployer_task or []
    funder_info = await funder_task

    links: list[dict[str, Any]] = []

    # ── Check 1: same deployer across chains ──
    other_chains = [d for d in deployer_rows if d["chain"] != chain_norm]
    if target_chains and other_chains:
        target_set = set(targets)
        other_chains = [d for d in other_chains if d["chain"] in target_set]
    if other_chains:
        conf = _deployer_confidence([d["chain"] for d in other_chains])
        for deploy in other_chains:
            links.append(
                {
                    "chain": deploy["chain"],
                    "address": addr,  # same deployer address on the other chain
                    "evidence_type": "same_deployer",
                    "confidence": conf,
                    "evidence": deploy["evidence"],
                    "details": {
                        "tokens_deployed": deploy["tokens_deployed"],
                        "first_deployed_ts": deploy["first_deployed_ts"],
                    },
                }
            )

    # ── Check 2: same funder across chains ──
    if funder_info is not None and funder_info.get("funder"):
        candidates = await run_sync(_query_same_funder_candidates, funder_info["funder"], chain_norm, targets or None)
        for cand in candidates or []:
            links.append(
                {
                    "chain": cand["chain"],
                    "address": cand["address"],
                    "evidence_type": "same_funder",
                    "confidence": _CONFIDENCE_SAME_FUNDER,
                    "evidence": cand["evidence"],
                    "details": {
                        "funder": funder_info["funder"],
                        "first_funder_ts": funder_info["first_ts"],
                    },
                }
            )

    # ── Check 3: behavioral fingerprint confirmation (v2) ──
    # When the deployer check found candidate chains, compute the wallet's
    # behavioral fingerprint on the source chain and on each candidate chain,
    # and emit a `behavioral` link when the cosine similarity is high enough.
    source_fp: dict[str, Any] | None = None
    if other_chains:
        try:
            source_fp = await compute_behavioral_fingerprint(addr, chain_norm)
            if source_fp.get("result") == "scanned":
                for deploy in other_chains[:_MAX_BEHAVIORAL_CONFIRMATIONS]:
                    target_fp = await compute_behavioral_fingerprint(addr, deploy["chain"])
                    if target_fp.get("result") != "scanned":
                        continue
                    similarity = compare_fingerprints(source_fp, target_fp)
                    if similarity < BEHAVIORAL_MIN_SIMILARITY:
                        continue
                    matches = matched_features(source_fp, target_fp)
                    links.append(
                        {
                            "chain": deploy["chain"],
                            "address": addr,
                            "evidence_type": "behavioral",
                            "confidence": round(CONFIDENCE_BEHAVIORAL * similarity, 2),
                            "evidence": "",
                            "details": {
                                "similarity": similarity,
                                "matched_features": matches,
                                "source_fingerprint_confidence": source_fp.get("confidence"),
                                "target_fingerprint_confidence": target_fp.get("confidence"),
                            },
                        }
                    )
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            RuntimeError,
            CHDriverError,
        ) as exc:
            # Behavioral confirmation must never break resolution
            logger.debug("cross_chain_entity_behavioral_failed: %s", exc)

    # ── Dedupe: same (chain, address, evidence_type) kept once ──
    seen: set[tuple[str, str, str]] = set()
    unique_links: list[dict[str, Any]] = []
    for link in links:
        key = (link["chain"], link["address"], link["evidence_type"])
        if key in seen:
            continue
        seen.add(key)
        unique_links.append(link)
    links = unique_links

    entity_type = "multi_chain_actor" if links else "unknown"
    risk = _risk_level(links)

    payload: dict[str, Any] = {
        "result": "scanned",
        "address": addr,
        "source_chain": chain_norm,
        "target_chains": targets or None,
        "addresses": links,
        "entity_type": entity_type,
        "risk": risk,
        "behavioral_fingerprint": (
            source_fp.get("fingerprint") if source_fp and source_fp.get("result") == "scanned" else None
        ),
        "links_found": len(links),
        "elapsed_ms": round((time.monotonic() - _start) * 1000, 1),
    }

    # ── 4. Cache ──
    try:
        await _redis_set(cache_key, payload, ttl=REDIS_TTL_SECONDS)
    except (ConnectionError, OSError, TimeoutError, RedisError):  # caching must never break resolution
        logger.debug("cross_chain_entity_redis_set_failed", key=cache_key)

    _ENTITY_CACHE[cache_key] = {**payload, "_cached_at": time.time()}
    if len(_ENTITY_CACHE) > _ENTITY_CACHE_MAX:
        _common_clear_cache(_ENTITY_CACHE)

    return payload


def _risk_level(links: list[dict[str, Any]]) -> str:
    """Heuristic v1 risk for the resolved entity (refined by v2 classification).

    No links → low; 1-2 links → medium; 3+ links → high. A same-funder link
    to a non-EVM chain (likely a bridge/launderer pattern) bumps one level.
    """
    if not links:
        return "low"
    level = "medium" if len(links) <= 2 else "high"
    if any(link["evidence_type"] == "same_funder" and link["chain"] not in _EVM_CHAINS for link in links):
        level = "high" if level == "medium" else level
    return level


# ═══════════════════════════════════════════════════════════════════════════
# TEST / MONITORING HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Expose cache stats for monitoring."""
    return _common_get_cache_stats(
        _ENTITY_CACHE,
        _ENTITY_CACHE_MAX,
        _ENTITY_CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


async def clear_cache() -> None:
    """Clear the in-process mirror (Redis entries expire on their own TTL)."""
    _common_clear_cache(_ENTITY_CACHE)
