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
    _get_ch_client,
    _normalize_chain,
)
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    redis_get,
    redis_set,
    run_sync,
)
from rugmunchbot._vendor.scanner_engines.launchpad_registry import (
    lookup_launchpad,
)
from rugmunchbot._vendor.scanner_engines.rpc_router import rpc_call

logger = get_logger(__name__)

_redis_get = redis_get


_redis_set = redis_set


REDIS_TTL_SECONDS = 3600  # 1 hour


REDIS_CACHE_PREFIX = "launchpad"


_RESOLVE_CACHE: dict[str, dict[str, Any]] = {}


_RESOLVE_CACHE_TTL = 600  # 10 minutes in-process mirror


_RESOLVE_CACHE_MAX = 200


_QUERY_TIMEOUT_SECONDS = 10


async def resolve_launchpad_deployer(
    token_address: str,
    chain: str,
    deployer_address: str,
    deploy_tx: str | None = None,
) -> dict[str, Any] | None:
    """Resolve a launchpad factory deployer to the real creator EOA.

    Args:
        token_address: The token/mint/contract address (used as the cache key
            and, on Solana, to locate the creation transaction).
        chain: Chain name (``solana``, ``ethereum``, ``bsc``, ...).
        deployer_address: The address recorded in ``deployer_events`` — when it
            is a known launchpad factory, we trace back to the real deployer.
        deploy_tx: Optional known creation transaction hash. When omitted we
            look it up from ``deployer_events`` (EVM) or derive it from the
            token mint (Solana).

    Returns:
        ``None`` when the deployer is not a known factory or resolution fails.
        Otherwise::

            {
                "real_deployer": "0x...",          # EOA creator
                "launchpad": "pump.fun",           # registry label
                "confidence": 0.9,                 # 0-1 attribution confidence
                "evidence_tx": "0x...",            # creation transaction
                "chain": "solana",
            }
    """
    launchpad = lookup_launchpad(chain, deployer_address)
    if launchpad is None:
        return None

    chain_norm = _normalize_chain(chain)
    token_key = str(token_address or "").strip()
    if not token_key and not deploy_tx:
        logger.debug("launchpad_resolve: missing token_address and deploy_tx", chain=chain_norm)
        return None

    # Cache key: token when available, otherwise the deploy tx (resolution
    # anchored on a creation transaction is deterministic per tx).
    anchor = token_key or f"tx:{deploy_tx}"
    cache_key = f"{REDIS_CACHE_PREFIX}:{chain_norm}:{anchor}"
    cached = await _redis_get(cache_key)
    if cached is not None and isinstance(cached, dict) and cached.get("real_deployer"):
        return cast("dict[str, Any]", cached)

    if cache_key in _RESOLVE_CACHE:
        entry = _RESOLVE_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _RESOLVE_CACHE_TTL:
            return {k: v for k, v in entry.items() if not k.startswith("_")}

    try:
        if chain_norm == "solana":
            result = await _resolve_solana(token_key, deploy_tx)
        else:
            result = await _resolve_evm(token_key, chain_norm, deploy_tx)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("launchpad_resolve_failed %s: %s", token_key[:16], exc)
        return None

    if result is None:
        return None

    result["launchpad"] = launchpad
    result["chain"] = chain_norm

    await _redis_set(cache_key, result, REDIS_TTL_SECONDS)
    result["_cached_at"] = time.time()
    _RESOLVE_CACHE[cache_key] = result
    if len(_RESOLVE_CACHE) > _RESOLVE_CACHE_MAX:
        _RESOLVE_CACHE.pop(next(iter(_RESOLVE_CACHE)), None)

    logger.info(
        "launchpad_resolved",
        chain=chain_norm,
        launchpad=launchpad,
        token=token_key[:16] or deploy_tx,
        real_deployer=str(result["real_deployer"])[:10],
        confidence=result.get("confidence"),
    )
    return result


async def _resolve_solana(token_address: str, deploy_tx: str | None) -> dict[str, Any] | None:
    """Resolve a Solana launchpad token to its creator.

    The real creator is the fee payer of the token's creation transaction
    (the first signature for the mint). The pump.fun program itself is never
    the fee payer, so this is unambiguous in practice.
    """
    signature = deploy_tx or await _find_solana_creation_tx(token_address)
    if not signature:
        return None

    tx = await _get_solana_transaction(signature)
    if not tx:
        return None

    fee_payer = _extract_fee_payer(tx)
    if not fee_payer:
        return None

    return {
        "real_deployer": fee_payer,
        "confidence": 0.9,
        "evidence_tx": signature,
    }


async def _find_solana_creation_tx(token_address: str) -> str | None:
    """Return the creation signature for a token mint.

    ``getSignaturesForAddress`` returns newest-first, so the oldest signature
    in a bounded window is the creation transaction.
    """
    data = await rpc_call(
        "solana",
        "getSignaturesForAddress",
        [token_address, {"limit": 20}],
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if not data or not isinstance(data, dict):
        return None
    sigs = data.get("result") or []
    if not isinstance(sigs, list) or not sigs:
        return None
    oldest = sigs[-1]
    if isinstance(oldest, dict):
        return oldest.get("signature")
    return str(oldest)


async def _get_solana_transaction(signature: str) -> dict[str, Any] | None:
    """Fetch a Solana transaction in jsonParsed encoding."""
    data = await rpc_call(
        "solana",
        "getTransaction",
        [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if not data or not isinstance(data, dict):
        return None
    tx = data.get("result")
    if not isinstance(tx, dict):
        return None
    return tx


def _extract_fee_payer(tx: dict[str, Any]) -> str | None:
    """Extract the fee payer (real creator) from a jsonParsed Solana tx.

    ``message.accountKeys[0]`` is the fee payer; jsonParsed keys may be
    dicts (``{"pubkey": ...}``) or plain strings depending on encoding.
    """
    try:
        message = (tx.get("transaction") or {}).get("message") or {}
        account_keys = message.get("accountKeys") or []
        if not account_keys:
            return None
        first = account_keys[0]
        if isinstance(first, dict):
            return first.get("pubkey")
        return str(first)
    except (AttributeError, TypeError, KeyError, ValueError):
        return None


async def _resolve_evm(
    token_address: str,
    chain: str,
    deploy_tx: str | None,
) -> dict[str, Any] | None:
    """Resolve an EVM launchpad token to its real deployer (``tx.from``)."""
    tx_hash = deploy_tx
    if not tx_hash:
        tx_hash = await run_sync(_query_deploy_tx, token_address, chain)
    if not tx_hash:
        return None

    data = await rpc_call(
        chain,
        "eth_getTransactionByHash",
        [tx_hash],
        timeout=_QUERY_TIMEOUT_SECONDS,
    )
    if not data or not isinstance(data, dict):
        return None
    tx = data.get("result")
    if not isinstance(tx, dict):
        return None
    sender = tx.get("from")
    if not sender:
        return None

    return {
        "real_deployer": str(sender).lower(),
        "confidence": 1.0,
        "evidence_tx": tx_hash,
    }


def _query_deploy_tx(token_address: str, chain: str) -> str | None:
    """Return the deploy transaction hash for a token from deployer_events."""
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT argMin(tx_hash, block_timestamp)
            FROM wallet_memory.deployer_events
            WHERE chain = %(chain)s
              AND lower(token_address) = %(token)s
            LIMIT 1
            """,
            {"chain": chain, "token": token_address.lower()},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("launchpad_deploy_tx_query_failed: %s", exc)
        return None
    if rows and rows[0] and rows[0][0]:
        return str(rows[0][0])
    return None


def _query_deployer_eoa(token_address: str, chain: str) -> str | None:
    """Return the indexer-resolved deployer EOA for a token (or None).

    Reads the ``deployer_eoa`` column of ``deployer_events`` — the EOA the
    lite indexer resolved from ``eth_getTransactionByHash`` at ingest time.
    Prefers the earliest (creation) row.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT argMin(deployer_eoa, block_timestamp)
            FROM wallet_memory.deployer_events
            WHERE chain = %(chain)s
              AND lower(token_address) = %(token)s
              AND deployer_eoa != ''
            LIMIT 1
            """,
            {"chain": chain, "token": token_address.lower()},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("launchpad_deployer_eoa_query_failed: %s", exc)
        return None
    if rows and rows[0] and rows[0][0]:
        return str(rows[0][0])
    return None


def _query_deployer_eoa_by_deployer(deployer: str, chain: str) -> str | None:
    """Return the indexer-resolved EOA for a deployer address (or None).

    Used when the caller has no token context (e.g. the fingerprint engine
    receives a bare deployer address). Returns the earliest resolved
    ``deployer_eoa`` for any event recorded under ``deployer`` — the real
    creator when the recorded deployer was a factory/contract.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT argMin(deployer_eoa, block_timestamp)
            FROM wallet_memory.deployer_events
            WHERE chain = %(chain)s
              AND lower(deployer) = %(deployer)s
              AND deployer_eoa != ''
            LIMIT 1
            """,
            {"chain": chain, "deployer": deployer.lower()},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("launchpad_deployer_eoa_by_deployer_failed: %s", exc)
        return None
    if rows and rows[0] and rows[0][0]:
        return str(rows[0][0])
    return None


def _has_resolved_deployer_eoa(deployer: str, chain: str) -> bool:
    """True when the indexer already resolved a real EOA for this deployer.

    Structural factory detection WITHOUT the registry: if ``deployer_events``
    holds rows under this deployer with a populated ``deployer_eoa`` that
    differs from the deployer itself, the address is a factory/contract and
    the EOA is the real creator. This catches ANY launchpad/factory the
    indexer has seen, even when it is not in ``LAUNCHPAD_FACTORIES``.
    """
    eoa = _query_deployer_eoa_by_deployer(deployer, chain)
    return bool(eoa and eoa.lower() != str(deployer).lower())


def _query_factory_latest_deploy_tx(factory_address: str, chain: str) -> str | None:
    """Return the most recent deploy tx recorded for a factory deployer.

    Used when a caller only has the factory address (no token context), e.g.
    the deployer fingerprint engine. The resolved creator of the most recent
    deployment is the best available attribution.
    """
    client = _get_ch_client()
    if client is None:
        return None
    try:
        rows = client.execute(
            """
            SELECT tx_hash
            FROM wallet_memory.deployer_events
            WHERE lower(deployer) = %(factory)s
              AND chain = %(chain)s
              AND tx_hash != ''
            ORDER BY block_timestamp DESC
            LIMIT 1
            """,
            {"factory": factory_address.lower(), "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("launchpad_factory_latest_tx_failed: %s", exc)
        return None
    if rows and rows[0] and rows[0][0]:
        return str(rows[0][0])
    return None


