# DE-GODDED 2026-08-22: registry / per-chain resolution / universal
# resolution moved verbatim to launchpad_registry.py,
# launchpad_resolve_chain.py and launchpad_resolve_universal.py; this
# module stays the public facade (re-exports + cache stats). Tripwire in
# scripts/refresh-vendors.sh --check guards against vendor-refresh regrowth.
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

from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.lite_chain_indexer import (
    _get_ch_client,
)
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
)

logger = get_logger(__name__)

# Verbatim-split re-exports (2026-08-22): implementations live in sibling
# modules; this facade keeps the public import path stable.
from rugmunchbot._vendor.scanner_engines.launchpad_registry import (  # noqa: F401,E402
    _REGISTRY,
    _REGISTRY_PATH,
    LAUNCHPAD_FACTORIES,
    _load_registry,
    get_launchpad_registry,
    lookup_launchpad,
    reload_registry,
)
from rugmunchbot._vendor.scanner_engines.launchpad_resolve_chain import (  # noqa: F401,E402
    _QUERY_TIMEOUT_SECONDS,
    _RESOLVE_CACHE,
    _RESOLVE_CACHE_MAX,
    _RESOLVE_CACHE_TTL,
    REDIS_CACHE_PREFIX,
    REDIS_TTL_SECONDS,
    _extract_fee_payer,
    _find_solana_creation_tx,
    _get_solana_transaction,
    _has_resolved_deployer_eoa,
    _query_deploy_tx,
    _query_deployer_eoa,
    _query_deployer_eoa_by_deployer,
    _query_factory_latest_deploy_tx,
    _redis_get,
    _redis_set,
    _resolve_evm,
    _resolve_solana,
    resolve_launchpad_deployer,
)
from rugmunchbot._vendor.scanner_engines.launchpad_resolve_universal import (  # noqa: F401,E402
    _is_contract_deployer,
    _resolve_evm_universal,
    _resolve_solana_universal,
    _resolve_universal_inner,
    _universal_passthrough,
    resolve_deployer_universal,
)

logger = get_logger(__name__)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _RESOLVE_CACHE,
        _RESOLVE_CACHE_MAX,
        _RESOLVE_CACHE_TTL,
        extra={
            "redis_ttl_seconds": REDIS_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
            "registry_chains": sorted(_REGISTRY),
            "registry_factories": sum(len(v) for v in _REGISTRY.values()),
        },
    )


def clear_cache() -> None:
    """Clear the in-process resolution cache (for testing)."""
    _common_clear_cache(_RESOLVE_CACHE)


__all__ = [
    "LAUNCHPAD_FACTORIES",
    "clear_cache",
    "get_cache_stats",
    "get_launchpad_registry",
    "lookup_launchpad",
    "reload_registry",
    "resolve_deployer_universal",
    "resolve_launchpad_deployer",
]


