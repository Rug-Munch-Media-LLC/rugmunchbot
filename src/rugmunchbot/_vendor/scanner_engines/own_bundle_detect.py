# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_bundle_detect.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Own Bundle Buy Detection — ClickHouse-Powered Bundle Analysis
================================================================
Phase 3 of the Data Ownership Strategy.

Detects coordinated bundle buys from our own ClickHouse data:
  - Same-block multi-wallet buys (multiple wallets buying in same block)
  - Common funder detection (same source wallet funded multiple buyers)
  - Temporal clustering (purchases within tight time windows)
  - Distribution anomaly (skewed holder distribution suggesting bundling)

Architecture:
  - Queries evm_transfers for token purchase patterns
  - Uses block-level grouping to identify bundled launches
  - Falls back gracefully when data is insufficient

Usage:
    from rugmunchbot._vendor.scanner_engines.own_bundle_detect import detect_bundled_launch, get_own_bundle_analysis

    result = await get_own_bundle_analysis("0x...", "ethereum")
    # result = {
    #     "is_bundled": bool,
    #     "confidence": float,
    #     "atomic_block_score": float,
    #     "common_funder_score": float,
    #     "distribution_anomaly_score": float,
    #     "top3_holder_pct": float,
    #     "top10_holder_pct": float,
    #     "bundle_blocks": [...],
    #     "source": "own",
    # }

    # Launch-window bundle detection (used by the unified scanner pipeline):
    launch = await detect_bundled_launch("0x...", "ethereum")
    # launch = {
    #     "is_bundled": bool,
    #     "confidence": float,
    #     "launch_cluster_score": float,
    #     "first_time_buyer_pct": float,
    #     "similar_amount_score": float,
    #     "deployer_funded_pct": float,
    #     "common_funder_score": float,
    #     "deploy_block": int | None,
    #     "cluster_blocks": [...],
    #     "source": "own",
    # }

    # Same-slot buy concentration (Solana, Helius-backed; [REVIEW] fallback):
    same_slot = await detect_same_slot_bundler("mint...", "solana")
    # same_slot = {
    #     "check": "same_slot_bundler",
    #     "result": "scanned" | "unavailable",
    #     "same_slot": int | None,        # busiest same-slot number
    #     "same_slot_wallets": int,        # wallets buying in that slot
    #     "same_slot_sol_spent": float,    # SOL spent (approx)
    #     "same_slot_held_pct": float,     # current held % of supply
    #     "is_bundled": bool, "confidence": float, "bundle_wallets": int,
    #     "note": str,                     # "[REVIEW] ..." when unavailable
    # }

Data Sources:
  Primary: wallet_memory.evm_transfers (transfer events)
  Primary: wallet_memory.deployer_events (deployment info)

Degodding split (2026-08-13): ClickHouse queries moved to
``engines/_own_bundle_queries.py``, pure scoring to
``engines/_own_bundle_scoring.py``, and shared plumbing (lazy client,
chain normalisation, cache, constants) to ``engines/_own_bundle_common.py``.
This module is the public API only; every name is re-exported so
``bd._query_*`` / ``bd._score_*`` references in tests and callers keep
working (test_own_bundle_detect / test_scan_enhancements use them).
"""

from __future__ import annotations

import asyncio
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
    run_sync,
)
from rugmunchbot._vendor.scanner_engines._own_bundle_common import (
    _BUNDLE_CACHE,
    _BUNDLE_CACHE_MAX,
    _BUNDLE_CACHE_TTL,
    _LAUNCH_WINDOW_BLOCKS,  # noqa: F401  (re-exported for tests/callers)
    _QUERY_TIMEOUT_SECONDS,  # noqa: F401  (re-exported)
    _SIMILAR_AMOUNT_CV_BANDS,  # noqa: F401  (re-exported)
    _ch_client,
    _normalize_chain,
)
from rugmunchbot._vendor.scanner_engines._own_bundle_queries import (
    # Re-exported so ``bd._query_*`` references keep resolving. Tests
    # (test_scan_enhancements) patch these on the ``bd`` namespace and
    # dispatch on function identity — re-export preserves identity.
    _query_atomic_blocks,
    _query_common_funders,
    _query_holder_concentration,
    _query_launch_cluster,
)
from rugmunchbot._vendor.scanner_engines._own_bundle_scoring import (
    # Re-exported for tests that call them as bd.<name>.
    _compute_bundle_score,
    _compute_launch_bundle_verdict,
    _score_atomic_blocks,
    _score_common_funders,
    _score_holder_concentration,
    _score_launch_cluster,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_bundle_analysis(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Get bundle buy analysis from own ClickHouse data.

    Analyzes transfer events to detect coordinated buying patterns:
    - Atomic block buys: multiple wallets buying in the same block
    - Common funder: multiple wallets funded by the same source
    - Distribution anomaly: extreme top-holder concentration

    Args:
        token_address: Token contract address
        chain: Chain name
        force_fresh: Bypass cache

    Returns:
        {
            "is_bundled": bool,
            "confidence": float,  # 0-100
            "risk_label": str,
            "atomic_block_score": float,  # 0-100
            "common_funder_score": float,  # 0-100
            "temporal_score": float,  # 0-100
            "distribution_anomaly_score": float,  # 0-100
            "concentration_score": float,  # 0-100
            "top3_holder_pct": float,
            "top10_holder_pct": float,
            "holder_count": int,
            "bundle_blocks": [{"block": int, "buyers": int, "buys": int}],
            "common_funders": [{"funder": str, "funded_count": int}],
            "source": str,
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    # Check cache
    cache_key = f"{token_address}:{chain}"
    if not force_fresh and cache_key in _BUNDLE_CACHE:
        cached = _BUNDLE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _BUNDLE_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "is_bundled": False,
        "confidence": 0.0,
        "risk_label": "unknown",
        "atomic_block_score": 0.0,
        "common_funder_score": 0.0,
        "temporal_score": 0.0,
        "distribution_anomaly_score": 0.0,
        "concentration_score": 0.0,
        "top3_holder_pct": 0.0,
        "top10_holder_pct": 0.0,
        "holder_count": 0,
        "bundle_blocks": [],
        "common_funders": [],
        "source": "unavailable",
    }

    try:
        # Run all queries in parallel
        atomic_task = asyncio.create_task(run_sync(_query_atomic_blocks, token_address, chain))
        funder_task = asyncio.create_task(run_sync(_query_common_funders, token_address, chain))
        holder_task = asyncio.create_task(run_sync(_query_holder_concentration, token_address, chain))
        launch_task = asyncio.create_task(run_sync(_query_launch_cluster, token_address, chain))

        atomic_blocks, funders, holder_data, launch_cluster = await asyncio.gather(
            atomic_task,
            funder_task,
            holder_task,
            launch_task,
        )

        # Compute scores
        if atomic_blocks:
            _score_atomic_blocks(result, atomic_blocks)

        if funders:
            _score_common_funders(result, funders)

        if holder_data:
            _score_holder_concentration(result, holder_data)

        if launch_cluster:
            _score_launch_cluster(result, launch_cluster)

        # Aggregate score
        _compute_bundle_score(result)

        if result["atomic_block_score"] > 0 or result["common_funder_score"] > 0:
            result["source"] = "own"

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
        logger.debug("own_bundle_query_failed for %s: %s", token_address[:16], exc)
        result["source"] = "unavailable"

    # Cache
    _cache_bundle(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_bundle_detect_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        bundled=result["is_bundled"],
        confidence=result["confidence"],
    )

    return result


async def detect_bundled_launch(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Detect a coordinated bundle launch (sniper/bot buys at deployment).

    Analyzes the FIRST ``_LAUNCH_WINDOW_BLOCKS`` blocks after the token's
    deploy event (from ``deployer_events``; falls back to the token's first
    transfer block) and flags coordinated clusters:

      (a) multiple distinct wallets buying in the same launch blocks,
      (b) wallets whose first-ever on-chain tx is this token (no prior
          token history),
      (c) launch buyers funded by the same wallet (deployer or a shared
          common funder),
      (d) similar purchase amounts (bot-sized buys — low coefficient of
          variation).

    Best-effort: returns an ``unavailable`` result when ClickHouse has no
    deploy/transfer data for the token.

    Args:
        token_address: Token contract address
        chain: Chain name
        force_fresh: Bypass cache

    Returns:
        {
            "check": "bundle_launch",
            "result": "scanned" | "unavailable" | "error",
            "source": "own",
            "token_address": str,
            "chain": str,
            "is_bundled": bool,
            "confidence": float,  # 0-100
            "risk_label": str,
            "launch_cluster_score": float,  # 0-100
            "first_time_buyer_pct": float,
            "similar_amount_score": float,  # 0-100
            "deployer_funded_pct": float,
            "common_funder_score": float,  # 0-100
            "deploy_block": int | None,
            "cluster_blocks": [{"block_number": int, "buyer_count": int}],
            "launch_buyers": int,
            "common_funders": [...],
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"launch:{token_address}:{chain}"
    if not force_fresh and cache_key in _BUNDLE_CACHE:
        cached = _BUNDLE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _BUNDLE_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "bundle_launch",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain,
        "is_bundled": False,
        "confidence": 0.0,
        "risk_label": "low",
        "launch_cluster_score": 0.0,
        "first_time_buyer_pct": 0.0,
        "similar_amount_score": 0.0,
        "deployer_funded_pct": 0.0,
        "common_funder_score": 0.0,
        "deploy_block": None,
        "cluster_blocks": [],
        "launch_buyers": 0,
        "common_funders": [],
    }

    try:
        cluster_task = asyncio.create_task(run_sync(_query_launch_cluster, token_address, chain))
        funder_task = asyncio.create_task(run_sync(_query_common_funders, token_address, chain))

        cluster, funders = await asyncio.gather(cluster_task, funder_task)

        if cluster:
            _score_launch_cluster(result, cluster)
        if funders:
            _score_common_funders(result, funders)

        _compute_launch_bundle_verdict(result)

        # ``scanned`` means the launch-window queries RAN. A clean launch
        # (cluster/funder rows present, zero scores) is a legitimate "no
        # bundle" verdict — not an outage. Only stay ``unavailable`` when no
        # query returned data (no deploy block / no ClickHouse) or the block
        # raised above.
        if cluster is not None or funders is not None:
            result["result"] = "scanned"
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
        logger.debug("launch_bundle_query_failed for %s: %s", token_address[:16], exc)
        result["result"] = "unavailable"

    _cache_bundle(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "launch_bundle_detect_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        bundled=result["is_bundled"],
        launch_score=result["launch_cluster_score"],
    )

    return result


# ═══════════════════════════════════════════════════════════════════════════
# SAME-SLOT BUNDLER (Solana — Helius-backed)
# ═══════════════════════════════════════════════════════════════════════════

#: Max Helius transactions parsed per same-slot scan (rate-limit budget).
_SAME_SLOT_MAX_TXS = 15
#: Max same-slot wallets checked for current holdings.
_SAME_SLOT_MAX_WALLETS = 8


async def detect_same_slot_bundler(
    token_address: str,
    chain: str,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Detect same-slot buy concentration (Solana) from Helius data.

    Groups recent buy transactions by Solana slot (block) and reports the
    busiest same-slot cluster: distinct wallets buying in the same slot,
    total SOL they spent (approximate — includes fees), and their current
    combined held % of the token supply.

    Data source: Helius RPC via ``app.chain_client.ChainClient`` (already
    integrated — rate-limited, circuit-breaker protected, no new dep).

    When Helius is unreachable (or the chain is not Solana), the result
    degrades to an ``unavailable`` payload that surfaces the existing
    bundle-detection verdict (wallet count + confidence from
    :func:`detect_bundled_launch`) and notes the slot-level detail as
    ``[REVIEW]``.

    Args:
        token_address: Token mint address.
        chain: Chain name.
        force_fresh: Bypass cache.

    Returns:
        {
            "check": "same_slot_bundler",
            "result": "scanned" | "unavailable",
            "source": "helius" | "own",
            "slot_analysis_source": "helius" | "unavailable",
            "same_slot": int | None,          # busiest same-slot number
            "same_slot_wallets": int,         # distinct wallets in that slot
            "same_slot_sol_spent": float,     # SOL they spent (approx)
            "same_slot_held_pct": float,      # their current held % of supply
            "slots": [...],                   # slot-concentration detail
            "is_bundled": bool,               # existing bundle verdict
            "confidence": float,              # existing confidence
            "bundle_wallets": int,            # existing launch wallet count
            "note": str,                      # "[REVIEW] ..." when unavailable
        }
    """
    token_address = token_address.strip()
    chain = _normalize_chain(chain)
    _start = time.monotonic()

    cache_key = f"same_slot:{token_address}:{chain}"
    if not force_fresh and cache_key in _BUNDLE_CACHE:
        cached = _BUNDLE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _BUNDLE_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "same_slot_bundler",
        "result": "unavailable",
        "source": "own",
        "slot_analysis_source": "unavailable",
        "same_slot": None,
        "same_slot_wallets": 0,
        "same_slot_sol_spent": 0.0,
        "same_slot_held_pct": 0.0,
        "slots": [],
        "is_bundled": False,
        "confidence": 0.0,
        "bundle_wallets": 0,
        "note": "Same-slot detail unavailable - Helius data source offline",
    }

    # Surface the existing bundle-detection verdict (wallet count +
    # confidence) regardless of the slot-level outcome.
    try:
        launch = await detect_bundled_launch(token_address, chain, force_fresh=force_fresh)
        if isinstance(launch, dict):
            result["is_bundled"] = bool(launch.get("is_bundled"))
            result["confidence"] = float(launch.get("confidence") or 0)
            result["bundle_wallets"] = int(launch.get("launch_buyers") or 0)
    except Exception as exc:  # pragma: no cover - defensive; never break the check
        logger.debug("same_slot_bundle_fallback_failed: %s", exc)

    if chain != "solana":
        result["note"] = "Same-slot analysis is Solana-only"
        _cache_bundle(cache_key, result)
        return result

    try:
        concentration = await _query_same_slot_concentration(token_address)
        if concentration is not None:
            result.update(concentration)
            result["result"] = "scanned"
            result["source"] = "helius"
            result["slot_analysis_source"] = "helius"
            result["note"] = ""
    except Exception as exc:  # pragma: no cover - defensive; never break the check
        logger.debug("same_slot_bundler_failed for %s: %s", token_address[:16], exc)
        result["note"] = "Same-slot detail unavailable - Helius data source offline"

    _cache_bundle(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "same_slot_bundler_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain,
        scanned=result["result"] == "scanned",
        wallets=result["same_slot_wallets"],
    )
    return result


async def _query_same_slot_concentration(mint: str) -> dict[str, Any] | None:
    """Query same-slot buy concentration for a Solana mint via Helius.

    Best-effort: any step failing (rate limit, circuit breaker, parse error)
    degrades to ``None`` so the caller surfaces the ``[REVIEW]`` fallback.
    """
    try:
        from app.chain_client import ChainClient
    except ImportError:  # pragma: no cover
        return None

    client = ChainClient()

    try:
        sig_resp = await client.rpc_call("getSignaturesForAddress", [mint, {"limit": 50}])
    except Exception:
        return None
    if not isinstance(sig_resp, dict) or not sig_resp.get("result"):
        return None
    sigs = sig_resp["result"][:_SAME_SLOT_MAX_TXS]

    # ── Per-tx: slot, signer, SOL spent, token balance delta ──
    buys: list[dict[str, Any]] = []
    for sig_info in sigs:
        signature = sig_info.get("signature") if isinstance(sig_info, dict) else None
        if not signature:
            continue
        try:
            tx_resp = await client.rpc_call(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
        except Exception as exc:
            logger.debug("same_slot_tx_fetch_failed for %s: %s", signature[:16], exc)
            continue
        if not isinstance(tx_resp, dict) or not tx_resp.get("result"):
            continue
        parsed = _parse_solana_buy_tx(tx_resp["result"], mint)
        if parsed:
            buys.append(parsed)

    if not buys:
        return None

    # ── Group by slot ──
    by_slot: dict[int, dict[str, Any]] = {}
    for b in buys:
        slot = b["slot"]
        entry = by_slot.setdefault(slot, {"slot": slot, "wallets": {}, "sol_spent": 0.0})
        entry["wallets"][b["signer"]] = b
        entry["sol_spent"] += b["sol_spent"]

    top_slot, top_entry = max(by_slot.items(), key=lambda kv: (len(kv[1]["wallets"]), kv[1]["sol_spent"]))
    top_wallets = list(top_entry["wallets"].keys())
    sol_spent = round(top_entry["sol_spent"], 4)

    clusters = [
        {
            "slot": slot,
            "wallets": list(e["wallets"].keys()),
            "sol_spent": round(e["sol_spent"], 4),
        }
        for slot, e in sorted(
            by_slot.items(),
            key=lambda kv: (len(kv[1]["wallets"]), kv[1]["sol_spent"]),
            reverse=True,
        )
        if len(e["wallets"]) >= 2
    ]

    # ── Current held % of the same-slot wallets ──
    total_supply = 0.0
    try:
        supply_resp = await client.rpc_call("getTokenSupply", [mint])
        if isinstance(supply_resp, dict) and supply_resp.get("result"):
            total_supply = float((supply_resp["result"].get("value") or {}).get("uiAmount") or 0)
    except Exception:
        total_supply = 0.0

    held_total = 0.0
    for wallet in top_wallets[:_SAME_SLOT_MAX_WALLETS]:
        try:
            acct_resp = await client.rpc_call(
                "getTokenAccountsByOwner",
                [wallet, {"mint": mint}, {"encoding": "jsonParsed"}],
            )
        except Exception as exc:
            logger.debug("same_slot_holder_fetch_failed for %s: %s", wallet[:16], exc)
            continue
        if not isinstance(acct_resp, dict):
            continue
        for acct in (acct_resp.get("result") or {}).get("value") or []:
            info = (acct.get("account") or {}).get("data") or {}
            amount = ((info.get("parsed") or {}).get("info") or {}).get("tokenAmount") or {}
            try:
                held_total += float(amount.get("uiAmount") or 0)
            except (TypeError, ValueError):
                continue
    held_pct = round(held_total / total_supply * 100, 2) if total_supply > 0 else 0.0

    return {
        "same_slot": top_slot,
        "same_slot_wallets": len(top_wallets),
        "same_slot_sol_spent": sol_spent,
        "same_slot_held_pct": held_pct,
        "slots": clusters[:_SAME_SLOT_MAX_TXS],
    }


def _parse_solana_buy_tx(tx: dict[str, Any], mint: str) -> dict[str, Any] | None:
    """Extract (slot, signer, sol_spent, token_delta) from a Helius tx.

    A "buy" is a transaction where the signer's balance of ``mint`` increased
    (postTokenBalances > preTokenBalances). SOL spent is the signer's
    pre/post SOL balance delta in lamports (includes fees — approximate).
    Returns ``None`` for non-buys / unparseable txs.
    """
    try:
        slot = int(tx.get("slot") or 0)
        meta = tx.get("meta") or {}
        msg = (tx.get("transaction") or {}).get("message") or {}
        account_keys = msg.get("accountKeys") or []
        if not account_keys or not meta:
            return None

        signer_idx = 0
        for i, acct in enumerate(account_keys):
            if isinstance(acct, dict) and acct.get("signer"):
                signer_idx = i
                break
        signer = account_keys[signer_idx]
        if isinstance(signer, dict):
            signer = signer.get("pubkey") or ""
        if not signer:
            return None

        pre_bal = meta.get("preBalances") or []
        post_bal = meta.get("postBalances") or []
        if signer_idx >= len(pre_bal) or signer_idx >= len(post_bal):
            return None
        sol_spent = (float(pre_bal[signer_idx] or 0) - float(post_bal[signer_idx] or 0)) / 1e9
        if sol_spent <= 0:
            return None

        def _token_balance(balances: list[Any]) -> float:
            total = 0.0
            for entry in balances or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("mint") != mint:
                    continue
                if entry.get("owner") and entry.get("owner") != signer:
                    continue
                try:
                    total += float((entry.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                except (TypeError, ValueError):
                    continue
            return total

        token_delta = _token_balance(meta.get("postTokenBalances") or []) - _token_balance(
            meta.get("preTokenBalances") or []
        )
        if token_delta <= 0:
            return None  # not a buy for this mint

        return {
            "slot": slot,
            "signer": signer,
            "sol_spent": round(sol_spent, 6),
            "token_delta": round(token_delta, 6),
        }
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        logger.debug("solana_buy_tx_parse_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_bundle(key: str, data: dict[str, Any]) -> None:
    data["_cached_at"] = time.time()
    _BUNDLE_CACHE[key] = data
    if len(_BUNDLE_CACHE) > _BUNDLE_CACHE_MAX:
        _BUNDLE_CACHE.pop(next(iter(_BUNDLE_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    return _common_get_cache_stats(
        _BUNDLE_CACHE,
        _BUNDLE_CACHE_MAX,
        _BUNDLE_CACHE_TTL,
        extra={"clickhouse_connected": _ch_client is not None},
    )


def clear_cache() -> None:
    _common_clear_cache(_BUNDLE_CACHE)
