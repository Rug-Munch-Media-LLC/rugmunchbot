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

import json
from pathlib import Path

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.lite_chain_indexer import (
    _normalize_chain,
)
from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)


LAUNCHPAD_FACTORIES: dict[str, dict[str, str]] = {
    "solana": {
        # pump.fun program ID — every token is created by sending the initial
        # transaction to this program; the fee payer is the real creator.
        "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P": "pump.fun",
        # Add more Solana launchpads as discovered (e.g. moonshot, pump it).
    },
    "ethereum": {
        # EVM factory addresses for launchpads (bags.fm etc.) — lowercase hex.
    },
    "bsc": {
        # BSC factory addresses
    },
}


_REGISTRY_PATH = Path(__file__).parent / "launchpad_factories.json"


def _load_registry() -> dict[str, dict[str, str]]:
    """Merge the JSON registry file on top of the in-code defaults.

    JSON keys win (so operators can update a launchpad's label or add new
    factories). Missing/corrupt file falls back to the in-code dict.
    """
    merged: dict[str, dict[str, str]] = {chain: dict(factories) for chain, factories in LAUNCHPAD_FACTORIES.items()}
    try:
        if _REGISTRY_PATH.exists():
            raw = json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
            for chain, factories in raw.items():
                if not isinstance(factories, dict):
                    continue
                merged.setdefault(str(chain), {}).update({str(k): str(v) for k, v in factories.items()})
    except (OSError, ValueError) as exc:
        logger.debug("launchpad_registry_load_failed: %s", exc)
    return merged


_REGISTRY: dict[str, dict[str, str]] = _load_registry()


def get_launchpad_registry() -> dict[str, dict[str, str]]:
    """Return the active launchpad registry (chain → factory → name)."""
    return {chain: dict(factories) for chain, factories in _REGISTRY.items()}


def reload_registry() -> dict[str, dict[str, str]]:
    """Reload the JSON registry file (runtime update path)."""
    global _REGISTRY
    _REGISTRY = _load_registry()
    return get_launchpad_registry()


def lookup_launchpad(chain: str, address: str) -> str | None:
    """Return the launchpad name for a factory address, or None.

    Solana addresses are base58 and case-sensitive, so a naive lowercase
    comparison would miss the canonical pump.fun program ID when callers
    pre-lowercase deployer addresses. Matching against our *own* registry is
    safe case-insensitively (the known factories are unique constants), so we
    try exact first, then case-insensitive, for every chain.
    """
    chain_norm = _normalize_chain(chain)
    factories = _REGISTRY.get(chain_norm, {})
    if not factories or not address:
        return None
    direct = factories.get(address)
    if direct is not None:
        return direct
    lowered = address.lower()
    for key, name in factories.items():
        if key.lower() == lowered:
            return name
    return None


