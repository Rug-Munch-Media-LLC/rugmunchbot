"""_solana_onchain.py split part: enrich_scan_solana."""

from __future__ import annotations

import asyncio
from typing import Any

from .solanaonchain_p1 import _BUNDLE_BUDGET_S, _CORE_BUDGET_S, logger, resolve_mint_facts
from .solanaonchain_p2 import resolve_deployer, resolve_holder_stats
from .solanaonchain_p3 import detect_same_slot_bundle
from .solanaonchain_seam import seam


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
        # Two-tier budgeting: the light, high-value resolvers (authority
        # facts, holder concentration, deployer) get their own tight budget
        # so the heavy same-slot bundle scan (up to 30 transaction fetches)
        # can never starve them on rate-limited public RPC.
        facts, holders, deployer = await asyncio.wait_for(
            asyncio.gather(
                seam("resolve_mint_facts", resolve_mint_facts)(address),
                seam("resolve_holder_stats", resolve_holder_stats)(address),
                seam("resolve_deployer", resolve_deployer)(address),
                return_exceptions=True,
            ),
            timeout=_CORE_BUDGET_S,
        )
        try:
            bundle = await asyncio.wait_for(
                seam("detect_same_slot_bundle", detect_same_slot_bundle)(address),
                timeout=_BUNDLE_BUDGET_S,
            )
        except Exception:
            bundle = None
    except Exception:
        return result
    for i, val in enumerate((facts, holders, deployer)):
        if isinstance(val, BaseException):
            logger.debug("solana resolver %d failed: %s", i, val)
            if i == 0:
                facts = None
            elif i == 1:
                holders = None
            elif i == 2:
                deployer = None
            else:
                bundle = None

    provenance: dict[str, Any] = {}

    if isinstance(facts, dict):
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

    if isinstance(holders, dict):
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

    if deployer and not (result.get("deployer_address") or result.get("dev_wallet")):
        result["deployer_address"] = deployer
        provenance["deployer_resolved"] = True

    # Lite Chain Indexer fallback layer: when the live RPC path could NOT
    # resolve the deployer, query LCI's ClickHouse store (indexed from its
    # own 62-endpoint free-RPC pool) for the token's deployer events.
    if not (deployer or result.get("deployer_address") or result.get("dev_wallet")):
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

    if isinstance(bundle, dict):
        existing = result.get("bundle_analysis")
        if isinstance(existing, dict) and existing.get("is_bundled"):
            # Backend already proved a bundle — keep its richer verdict.
            provenance["bundle_source"] = "backend"
        else:
            result["bundle_analysis"] = bundle
            result["bundle_detected"] = bool(bundle.get("is_bundled"))
            provenance["bundle_source"] = "same_slot_rpc"

    if provenance:
        result.setdefault("onchain", {}).update(provenance)
    return result
