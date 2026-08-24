# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_deployer_fingerprint_profiles.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Deployer-profile data / lookup for deployer_fingerprint (degodding 2026-08-14).

Holds the "profile" concerns of ``engines/deployer_fingerprint.py``:

  - ``_build_funder_profile`` — enrich the raw first-funder row into the
    deployer's funding-source profile (CEX label, freshness, multiplicity).
  - Launchpad factory resolution helpers — ``_is_known_launchpad_factory``,
    ``_launchpad_name``, ``_resolve_launchpad_context``, and
    ``_resolve_structural_eoa`` trace a factory/contract deployer to the real
    creator EOA before fingerprinting.

``deployer_fingerprint.py`` re-exports every name so
``deployer_fingerprint._build_funder_profile`` etc. keep resolving for tests
and callers. Imports are one-directional: this module imports the pure
helpers and the ClickHouse query layer; it never imports
``deployer_fingerprint``.
"""

from __future__ import annotations

import asyncio
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import run_sync, to_iso
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_helpers import _classify_cex
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_queries import (
    _query_funder_label,
    _query_funder_tx_count,
    _query_same_funder_deployers,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# FUNDER PROFILE
# ═══════════════════════════════════════════════════════════════════════════


async def _build_funder_profile(
    funder: dict[str, Any] | None,
    chain_norm: str,
    deployer_address: str,
) -> dict[str, Any]:
    """Enrich the raw funder row with label, freshness, and multiplicity."""
    profile: dict[str, Any] = {
        "funder_address": None,
        "funding_tx": None,
        "funding_ts": None,
        "funding_token": None,
        "funding_native": False,
        "funder_is_cex": False,
        "cex_label": None,
        "funder_is_fresh": None,
        "funder_tx_count": None,
        "same_funder_multiple": None,
    }

    if not funder:
        return profile

    funder_addr = funder.get("from_address")
    if not funder_addr or funder_addr.lower() == deployer_address:
        return profile

    profile["funder_address"] = funder_addr
    profile["funding_tx"] = funder.get("first_tx")
    profile["funding_ts"] = to_iso(funder.get("first_ts"))
    profile["funding_token"] = funder.get("first_token")
    profile["funding_native"] = bool(funder.get("is_native"))

    # Parallel enrichment queries (all bounded, graceful on failure)
    label_task = asyncio.create_task(run_sync(_query_funder_label, funder_addr))
    tx_count_task = asyncio.create_task(run_sync(_query_funder_tx_count, funder_addr))
    overlap_task = asyncio.create_task(run_sync(_query_same_funder_deployers, funder_addr, chain_norm))
    labels_result: Any
    tx_count_result: Any
    overlap_result: Any
    labels_result, tx_count_result, overlap_result = await asyncio.gather(
        label_task, tx_count_task, overlap_task, return_exceptions=True
    )

    labels: list[dict[str, Any]] = []
    if not isinstance(labels_result, BaseException) and labels_result:
        labels = labels_result
    tx_count: int | None = None
    if not isinstance(tx_count_result, BaseException):
        tx_count = tx_count_result
    overlap: int = 0
    if not isinstance(overlap_result, BaseException) and overlap_result is not None:
        overlap = overlap_result

    # CEX classification from wallet_labels
    is_cex, cex_label = _classify_cex(labels)
    profile["funder_is_cex"] = is_cex
    profile["cex_label"] = cex_label
    profile["funder_labels"] = [
        {
            "label_name": lbl.get("label_name"),
            "label_category": lbl.get("label_category"),
            "source": lbl.get("source"),
        }
        for lbl in labels[:5]
    ]

    # Freshness: < 10 total transactions
    if tx_count is not None:
        profile["funder_tx_count"] = int(tx_count)
        profile["funder_is_fresh"] = int(tx_count) < 10

    # Same funder used by 2+ known deployers
    profile["same_funder_multiple"] = int(overlap or 0) >= 2

    return profile


# ═══════════════════════════════════════════════════════════════════════════
# LAUNCHPAD / STRUCTURAL RESOLUTION (factory → real creator EOA)
# ═══════════════════════════════════════════════════════════════════════════


async def _resolve_launchpad_context(deployer_address: str, chain_norm: str) -> dict[str, Any] | None:
    """Resolve a known launchpad factory to its real creator EOA.

    Delegates to ``launchpad_resolver.resolve_launchpad_deployer`` (lazy import
    to keep this engine's import graph light). When the caller has no token
    context, we anchor resolution on the factory's most recent recorded deploy
    transaction from ``deployer_events``.

    Returns ``None`` when the factory is not in the registry or the creator
    cannot be traced.
    """
    try:
        from rugmunchbot._vendor.scanner_engines.launchpad_resolver import (
            _query_factory_latest_deploy_tx,
            resolve_launchpad_deployer,
        )

        deploy_tx = await run_sync(_query_factory_latest_deploy_tx, deployer_address, chain_norm)
        return await resolve_launchpad_deployer("", chain_norm, deployer_address, deploy_tx=deploy_tx)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("deployer_fingerprint_launchpad_resolve_failed: %s", exc)
        return None


async def _resolve_structural_eoa(deployer_address: str, chain_norm: str) -> dict[str, Any] | None:
    """Structurally resolve a factory/contract deployer to its real creator EOA.

    No registry required: if the lite indexer already resolved ``deployer_eoa``
    for any event under this deployer (``tx.from`` of the creation tx at ingest
    time), that EOA is the real creator. Returns a launchpad-style context dict
    or ``None`` when the deployer is a plain EOA (its eoa equals itself) or the
    indexer has no data for it.
    """
    try:
        from rugmunchbot._vendor.scanner_engines.launchpad_resolver import (
            _has_resolved_deployer_eoa,
            _query_deployer_eoa_by_deployer,
        )

        if not await run_sync(_has_resolved_deployer_eoa, deployer_address, chain_norm):
            return None
        eoa = await run_sync(_query_deployer_eoa_by_deployer, deployer_address, chain_norm)
        if not eoa or eoa.lower() == deployer_address.lower():
            return None
        return {
            "real_deployer": eoa.lower(),
            "launchpad": None,
            "confidence": 1.0,
            "evidence_tx": None,
            "method": "deployer_eoa",
        }
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("deployer_fingerprint_structural_resolve_failed: %s", exc)
        return None


def _is_known_launchpad_factory(deployer_address: str, chain_norm: str) -> bool:
    """True when the deployer address is a registered launchpad factory.

    Non-lazy import is avoided at module load; the lookup itself is cheap.
    """
    try:
        from rugmunchbot._vendor.scanner_engines.launchpad_resolver import lookup_launchpad

        return lookup_launchpad(chain_norm or "ethereum", deployer_address) is not None
    except (ImportError, ValueError):
        return False


def _launchpad_name(deployer_address: str, chain_norm: str) -> str | None:
    """Return the launchpad label for a factory address (or None)."""
    try:
        from rugmunchbot._vendor.scanner_engines.launchpad_resolver import lookup_launchpad

        return lookup_launchpad(chain_norm or "ethereum", deployer_address)
    except (ImportError, ValueError):
        return None
