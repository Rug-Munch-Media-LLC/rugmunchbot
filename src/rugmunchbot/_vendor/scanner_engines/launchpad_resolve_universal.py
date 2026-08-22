# Split from the vendored launchpad_resolver.py during the de-godding
# program 2026-08-22. Blocks moved VERBATIM by AST line-span slicing — see
# the facade launchpad_resolver.py for provenance and re-exports.
# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/launchpad_resolver.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Launchpad Resolver — Trace Factory Deployments to the Real Creator
==================================================================
Launchpads (pump.fun, bags.fm, moonshot, ...) create tokens through a
factory contract / on-chain program. Our ``deployer_events`` table therefore
records the *factory* address as the deployer, not the real creator EOA.

This module resolves the factory back to the real deployer:

  - **Solana / pump.fun**: the pump.fun program is a well-known program ID.
    Every token mint's first transaction (creation) is sent to the program
    by the real creator, who is the transaction **fee payer**.
  - **EVM / bags.fm**: the factory contract emits the child token; the real
    deployer is ``tx.from`` of the creation transaction.

Two public entry points:

  * ``resolve_launchpad_deployer`` — factory-only resolution. Returns ``None``
    when the deployer is not a known launchpad factory (callers that already
    know they are handling a factory use this).
  * ``resolve_deployer_universal`` — UNIVERSAL resolution that works for ANY
    token on ANY platform on ANY chain. It never returns ``None``: it walks a
    structural fallback chain (factory trace → indexer-resolved ``deployer_eoa``
    → EVM contract creation trace → Solana creation fee payer → passthrough)
    and always returns the best available deployer address.

Resolution strategies per chain:

  * Solana — ``getSignaturesForAddress(mint)`` → oldest signature (creation),
    then ``getTransaction`` (jsonParsed) → ``transaction.message.accountKeys[0]``
    is the fee payer. Confidence 0.9 (fee payer is the creator for pump.fun;
    the program itself can never be the fee payer).
  * EVM — needs the deploy transaction hash. We look it up from
    ``deployer_events`` (``deploy_tx`` column) when the caller did not already
    provide it, then ``eth_getTransactionByHash`` → ``from``. Confidence 1.0
    (``tx.from`` is authoritative for contract creation on EVM).

Structural detection (not a factory list):

  The universal resolver deliberately does NOT rely on the registry to decide
  *whether* to trace. It checks:

    - ``deployer_eoa`` in ``deployer_events`` — the lite indexer already
      resolved the real EOA (tx ``from``) at ingest time; use it directly.
    - EVM: ``eth_getCode`` on the deployer — code present means the deployer is
      a factory/contract, so trace its creation transaction.
    - Solana: the creation transaction's fee payer is the real creator.

  The registry only adds a *label* (``launchpad: "pump.fun"``) and a
  higher-confidence fast path for known factories.

Registry:
  Known factory/program addresses live in ``LAUNCHPAD_FACTORIES`` (in code) and
  are overridable/extendable via the JSON registry file
  ``launchpad_factories.json`` next to this module. The JSON file is merged on
  top of the in-code dict, so operators can add newly-discovered launchpads
  without a code change.

Caching:
  - Redis ``launchpad:{chain}:{token}`` TTL 1h (async, graceful fallback).
  - In-process mirror (10 min) consistent with the other engines.

Usage:
    from rugmunchbot._vendor.scanner_engines.launchpad_resolver import (
        resolve_deployer_universal,
        resolve_launchpad_deployer,
    )

    result = await resolve_launchpad_deployer(token_address, "solana", factory_address)
    # → {"real_deployer": "...", "launchpad": "pump.fun", "confidence": 0.9, "evidence_tx": "..."}

    result = await resolve_deployer_universal(token_address, "ethereum", deployer_from_event)
    # → {"real_deployer": "...", "launchpad": ..., "confidence": ..., "evidence_tx": ..., "method": ...}

NOTE (degodding 2026-08-07): 838 lines. Split is deferred (per-launchpad resolvers
share the on-chain client + cache plumbing). If splitting, move each launchpad resolver
(pump.fun, bags.fm, moonshot, ...) into ``engines/launchpad_resolver/_resolvers.py``
keyed by launchpad id.
"""

from __future__ import annotations

import time
from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.lite_chain_indexer import (
    _normalize_chain,
)
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    run_sync,
)
from rugmunchbot._vendor.scanner_engines.launchpad_registry import (
    lookup_launchpad,
)
from rugmunchbot._vendor.scanner_engines.launchpad_resolve_chain import (
    _QUERY_TIMEOUT_SECONDS,
    _RESOLVE_CACHE,
    _RESOLVE_CACHE_MAX,
    _RESOLVE_CACHE_TTL,
    REDIS_CACHE_PREFIX,
    REDIS_TTL_SECONDS,
    _extract_fee_payer,
    _find_solana_creation_tx,
    _get_solana_transaction,
    _query_deploy_tx,
    _query_deployer_eoa,
    _query_deployer_eoa_by_deployer,
    _redis_get,
    _redis_set,
    resolve_launchpad_deployer,
)
from rugmunchbot._vendor.scanner_engines.rpc_router import rpc_call

logger = get_logger(__name__)


async def resolve_deployer_universal(
    token_address: str,
    chain: str,
    deployer_from_event: str = "",
    deploy_tx: str | None = None,
) -> dict[str, Any]:
    """Universal deployer resolution — works for ANY token on ANY platform.

    Unlike :func:`resolve_launchpad_deployer` (factory-only, returns ``None``
    for non-factories), this resolver NEVER returns ``None``. It walks a
    structural fallback chain and always returns the best available deployer:

      1. Known factory → trace through to the real creator (registry fast path).
      2. ``deployer_eoa`` in ``deployer_events`` → already resolved by the
         lite indexer at ingest time (``tx.from`` of the creation tx). Use it.
      3. EVM: deployer has code (it is a contract/factory, not an EOA) →
         resolve the token's creation transaction and return ``tx.from``.
      4. Solana: fee payer of the token's creation transaction (oldest
         signature for the mint) is the real creator.
      5. Fallback: return ``deployer_from_event`` as-is (passthrough).

    Args:
        token_address: Token / mint / contract address.
        chain: Chain name (``solana``, ``ethereum``, ``bsc``, ``base``, ...).
        deployer_from_event: Deployer address recorded in ``deployer_events``
            (may be a factory, a contract, an EOA, or empty for subsquid rows).
        deploy_tx: Optional known creation transaction hash.

    Returns:
        Always a dict (never ``None``)::

            {
                "real_deployer": "0x...",      # best available deployer
                "launchpad": "pump.fun",       # registry label or None
                "confidence": 0.0-1.0,          # attribution confidence
                "evidence_tx": "0x...",        # creation tx (or None)
                "chain": "ethereum",
                "method": "factory_trace" | "deployer_eoa" |
                          "evm_contract" | "evm_tx" | "solana_fee_payer" |
                          "passthrough",
                "resolved": bool,               # True when we improved on input
            }
    """
    chain_norm = _normalize_chain(chain) if chain else "ethereum"
    token_key = str(token_address or "").strip()
    deployer_key = str(deployer_from_event or "").strip()

    # Cache key: token when available, otherwise deploy tx (deterministic).
    anchor = token_key or f"tx:{deploy_tx}"
    cache_key = f"{REDIS_CACHE_PREFIX}:uni:{chain_norm}:{anchor}"
    cached = await _redis_get(cache_key)
    if cached is not None and isinstance(cached, dict) and cached.get("real_deployer"):
        return cast("dict[str, Any]", cached)

    if cache_key in _RESOLVE_CACHE:
        entry = _RESOLVE_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _RESOLVE_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    result = await _resolve_universal_inner(token_key, chain_norm, deployer_key, deploy_tx)

    # NEVER return None — passthrough is the last resort.
    if not result.get("real_deployer"):
        result = _universal_passthrough(deployer_key, chain_norm)

    await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
    result["_cached_at"] = time.time()
    _RESOLVE_CACHE[cache_key] = result
    if len(_RESOLVE_CACHE) > _RESOLVE_CACHE_MAX:
        _RESOLVE_CACHE.pop(next(iter(_RESOLVE_CACHE)), None)

    logger.debug(
        "deployer_universal_resolved",
        chain=chain_norm,
        token=token_key[:16] or deploy_tx,
        method=result.get("method"),
        real_deployer=str(result.get("real_deployer"))[:10],
        confidence=result.get("confidence"),
    )
    return result


async def _resolve_universal_inner(
    token_address: str,
    chain: str,
    deployer_from_event: str,
    deploy_tx: str | None,
) -> dict[str, Any]:
    """Run the universal fallback chain (steps 1-5). Never returns None."""
    # ── 1. Known factory → trace through (registry fast path) ──
    launchpad = lookup_launchpad(chain, deployer_from_event) if deployer_from_event else None
    if launchpad is not None:
        traced = await resolve_launchpad_deployer(
            token_address,
            chain,
            deployer_from_event,
            deploy_tx=deploy_tx,
        )
        if traced and traced.get("real_deployer"):
            traced["method"] = "factory_trace"
            traced["resolved"] = True
            return traced

    # ── 2. deployer_eoa already resolved by the indexer ──
    eoa = await run_sync(_query_deployer_eoa, token_address, chain) if token_address else None
    if not eoa and deployer_from_event:
        # No token context (fingerprint-style callers): look up by deployer.
        eoa = await run_sync(_query_deployer_eoa_by_deployer, deployer_from_event, chain)
    if eoa:
        # Only claim an improvement when the indexer resolved something
        # different from (or more than) what the caller already had.
        resolved = bool(deployer_from_event) and str(eoa).lower() != deployer_from_event.lower()
        return {
            "real_deployer": str(eoa).lower(),
            "launchpad": launchpad,
            "confidence": 1.0,
            "evidence_tx": deploy_tx,
            "chain": chain,
            "method": "deployer_eoa",
            "resolved": resolved,
        }

    # ── 3/4. Chain-specific structural fallbacks ──
    if chain == "solana":
        return await _resolve_solana_universal(token_address, deploy_tx, launchpad)
    if chain == "tron":
        # Tron is EVM-like: the creation transaction sender is the deployer.
        # We have no Tron RPC in the router, so use the deployer_events tx.
        return await _resolve_evm_universal(token_address, chain, deploy_tx, launchpad)

    return await _resolve_evm_universal(token_address, chain, deploy_tx, launchpad)


def _universal_passthrough(deployer_from_event: str, chain: str) -> dict[str, Any]:
    """Last-resort result — return the recorded deployer unchanged."""
    return {
        "real_deployer": deployer_from_event,
        "launchpad": None,
        "confidence": 0.0,
        "evidence_tx": None,
        "chain": chain,
        "method": "passthrough",
        "resolved": False,
    }


async def _resolve_solana_universal(
    token_address: str,
    deploy_tx: str | None,
    launchpad: str | None,
) -> dict[str, Any]:
    """Solana: fee payer of the token's creation transaction is the creator.

    Structural — works for ANY Solana program (pump.fun, moonshot, pump it,
    or an unknown launchpad): the mint's first transaction was submitted by
    the real creator, who is its fee payer.
    """
    if not token_address and not deploy_tx:
        return _universal_passthrough("", "solana")

    signature = deploy_tx or await _find_solana_creation_tx(token_address)
    if not signature:
        return _universal_passthrough("", "solana")

    tx = await _get_solana_transaction(signature)
    if not tx:
        return _universal_passthrough("", "solana")

    fee_payer = _extract_fee_payer(tx)
    if not fee_payer:
        return _universal_passthrough("", "solana")

    return {
        "real_deployer": fee_payer,
        "launchpad": launchpad,
        "confidence": 0.9,
        "evidence_tx": signature,
        "chain": "solana",
        "method": "solana_fee_payer",
        "resolved": True,
    }


async def _resolve_evm_universal(
    token_address: str,
    chain: str,
    deploy_tx: str | None,
    launchpad: str | None,
) -> dict[str, Any]:
    """EVM: resolve the token's creation tx and return ``tx.from``.

    Uses the caller-provided deploy tx, or looks it up from
    ``deployer_events``. The creation tx sender is authoritative for the
    deployer on every EVM chain.
    """
    if not token_address and not deploy_tx:
        return _universal_passthrough("", chain)

    tx_hash = deploy_tx
    if not tx_hash and token_address:
        tx_hash = await run_sync(_query_deploy_tx, token_address, chain)
    if not tx_hash:
        return _universal_passthrough("", chain)

    data = await rpc_call(
        chain,
        "eth_getTransactionByHash",
        [tx_hash],
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if not data or not isinstance(data, dict):
        return _universal_passthrough("", chain)
    tx = data.get("result")
    if not isinstance(tx, dict):
        return _universal_passthrough("", chain)
    sender = tx.get("from")
    if not sender:
        return _universal_passthrough("", chain)

    return {
        "real_deployer": str(sender).lower(),
        "launchpad": launchpad,
        "confidence": 1.0,
        "evidence_tx": tx_hash,
        "chain": chain,
        "method": "evm_contract" if _is_contract_deployer(tx, chain) else "evm_tx",
        "resolved": True,
    }


def _is_contract_deployer(tx: dict[str, Any], chain: str) -> bool:
    """Best-effort: True when the creation tx's ``to`` is a contract.

    A creation transaction that targets a factory contract (non-null ``to``
    with code) means the real deployer went through a factory — worth labeling
    as ``evm_contract``. A ``to`` of null means a direct contract deployment
    (tx creates the token itself).
    """
    to = tx.get("to")
    # ``to`` present: the deployer called a factory/contract. We do not
    # verify code here to keep this cheap; the method label is informational.
    return not (to is None or str(to).strip() in ("", "0x"))


