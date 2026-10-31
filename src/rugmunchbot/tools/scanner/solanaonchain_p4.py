"""_solana_onchain.py split part: enrich_scan_solana and its appliers."""

from __future__ import annotations

import asyncio
from typing import Any

from .solanaonchain_p1 import _BUNDLE_BUDGET_S, _CORE_BUDGET_S, logger, resolve_mint_facts
from .solanaonchain_p2 import resolve_deployer, resolve_holder_stats
from .solanaonchain_p3 import detect_same_slot_bundle
from .solanaonchain_seam import seam


async def _core_facts(address: str) -> tuple[Any, Any, Any]:
    """Authorities, holder concentration and deployer, under one tight budget."""
    return await asyncio.wait_for(
        asyncio.gather(
            seam("resolve_mint_facts", resolve_mint_facts)(address),
            seam("resolve_holder_stats", resolve_holder_stats)(address),
            seam("resolve_deployer", resolve_deployer)(address),
            return_exceptions=True,
        ),
        timeout=_CORE_BUDGET_S,
    )


async def _bundle(address: str) -> Any:
    """Same-slot bundle detection has its own budget so it cannot starve the rest."""
    try:
        return await asyncio.wait_for(
            seam("detect_same_slot_bundle", detect_same_slot_bundle)(address),
            timeout=_BUNDLE_BUDGET_S,
        )
    except Exception:
        return None


def _settle(index: int, value: Any) -> Any:
    """Log a resolver exception and degrade it to ``None`` (fail-open)."""
    if isinstance(value, BaseException):
        logger.debug("solana resolver %d failed: %s", index, value)
        return None
    return value


def _apply_mint_facts(result: dict[str, Any], facts: Any, provenance: dict[str, Any]) -> None:
    """Write authority facts and the Token-2022 transfer fee."""
    if not isinstance(facts, dict):
        return
    provenance["mint_facts"] = True
    mint_active = facts.get("mint_authority") is not None
    freeze_active = facts.get("freeze_authority") is not None
    # Authority facts are ground truth — they override provider guesses.
    result["mintable"] = mint_active
    result["owner_renounced"] = not mint_active
    result["mint_authority"] = facts.get("mint_authority")
    result["freezable"] = freeze_active
    result["can_blacklist"] = freeze_active
    result["freeze_authority"] = facts.get("freeze_authority")
    fee_pct = facts.get("transfer_fee_pct")
    if fee_pct is not None:
        result["buy_tax"] = fee_pct / 100.0
        result["sell_tax"] = fee_pct / 100.0
    elif facts.get("token_program") == "spl":
        # Classic SPL tokens have no protocol-level transfer tax.
        result.setdefault("buy_tax", 0.0)
        result.setdefault("sell_tax", 0.0)
    exts = facts.get("extensions") or []
    if exts:
        result["token_extensions"] = exts
    provenance["token_program"] = facts.get("token_program")


def _apply_holders(result: dict[str, Any], holders: Any, provenance: dict[str, Any]) -> None:
    """Write holder concentration, never overwriting a real provider value."""
    if not isinstance(holders, dict):
        return
    provenance["holder_stats"] = True
    if not result.get("top_holders"):
        result["top_holders"] = holders.get("top_holders") or []
    # Holder count: only fill when no provider reported one (never
    # overwrite a real provider count with ours, never fabricate).
    if holders.get("holder_count") is not None and not result.get("holders"):
        result["holders"] = int(holders["holder_count"])
    rs = result.get("risk_signals") if isinstance(result.get("risk_signals"), dict) else None
    if rs is not None and rs.get("top10_holder_pct") is None:
        rs["top10_holder_pct"] = holders.get("top_10_pct")
    oc = result.setdefault("onchain", {})
    oc["top_10_pct"] = holders.get("top_10_pct")
    oc["top_20_pct"] = holders.get("top_20_pct")
    if holders.get("holder_count") is not None:
        oc["holder_count"] = holders["holder_count"]


async def _apply_deployer(
    result: dict[str, Any], deployer: Any, provenance: dict[str, Any], address: str, chain: str
) -> None:
    """Write the deployer, falling back to Lite Chain Indexer history."""
    if deployer and not (result.get("deployer_address") or result.get("dev_wallet")):
        result["deployer_address"] = deployer
        provenance["deployer_resolved"] = True
        return

    # When the live RPC path could NOT resolve the deployer, query LCI's
    # ClickHouse store (indexed from its own free-RPC pool) for the token's
    # deployer events.
    if deployer or result.get("deployer_address") or result.get("dev_wallet"):
        return
    from rugmunchbot.tools.scanner._chain_pools import (
        lci_deployer_history,
        lci_deployer_summary,
    )

    try:
        lci_hist = await asyncio.wait_for(lci_deployer_history(address, chain), timeout=4.0)
    except Exception:
        lci_hist = None
    summary = lci_deployer_summary(lci_hist)
    if summary:
        result["deployer_history"] = summary
        provenance["deployer_source"] = "lite_chain_indexer"


def _apply_bundle(result: dict[str, Any], bundle: Any, provenance: dict[str, Any]) -> None:
    """Merge the same-slot bundle verdict, keeping a richer backend verdict."""
    if not isinstance(bundle, dict):
        return
    existing = result.get("bundle_analysis")
    if isinstance(existing, dict) and existing.get("is_bundled"):
        provenance["bundle_source"] = "backend"
        return
    result["bundle_analysis"] = bundle
    result["bundle_detected"] = bool(bundle.get("is_bundled"))
    provenance["bundle_source"] = "same_slot_rpc"


async def enrich_scan_solana(result: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Fill provider-missing scan fields from Solana RPC (fail-open).

    Entry point wired into both scan paths. Only acts on Solana tokens.
    Fills, in priority order (never overwrites a real provider value with
    a worse one):

    * ``mintable`` / ``owner_renounced`` — from mint authority presence
    * ``freezable`` / ``can_blacklist`` — from freeze authority presence
    * ``buy_tax`` / ``sell_tax`` — Token-2022 transfer fee (SPL classic
      tokens have no protocol-level tax: reported as 0.0 once resolved)
    * ``holders`` / ``top_holders`` — RPC largest-accounts concentration
    * ``deployer_address`` — creation-tx fee payer fallback
    * ``bundle_analysis`` — same-slot cluster detection merge
    * ``onchain`` — provenance block for the formatter/tests
    """
    if not isinstance(result, dict) or not address:
        return result
    if (chain or "").lower() not in ("solana", "sol") and not address.endswith("pump"):
        return result

    try:
        core = await _core_facts(address)
        bundle = await _bundle(address)
    except Exception:
        return result

    facts, holders, deployer = (_settle(i, val) for i, val in enumerate(core))
    provenance: dict[str, Any] = {}
    _apply_mint_facts(result, facts, provenance)
    _apply_holders(result, holders, provenance)
    await _apply_deployer(result, deployer, provenance, address, chain)
    _apply_bundle(result, bundle, provenance)
    if provenance:
        result.setdefault("onchain", {}).update(provenance)
    return result
