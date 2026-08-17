# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/_evidence.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Evidence-builder for unified scoring — module results → evidence dicts.

Extracted from ``unified/scoring.py`` (degodding wave 3, 2026-08-08) so the
explainability payload builder lives beside the other pure mapping helpers
instead of bloating the scoring module.

This module is a pure mapping: given a ``ScanResult`` it extracts the raw
findings backing each risk dimension (``security``, ``liquidity``,
``holders``, ``deployer``, ``market`` plus SENTINEL deep-forensic findings
and market context). Defensive: every field is optional; missing data yields
an empty dict. Never mutates ``r`` and never raises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot._vendor.unified_scanner.model import ScanResult


def build_evidence(r: ScanResult) -> dict[str, Any]:
    """Extract the raw findings backing each dimension (for explainability).

    Defensive: every field is optional; missing data yields an empty dict.
    The result is the ``evidence`` payload attached to a scan so users can
    audit exactly why a token scored the way it did.
    """
    ev: dict[str, Any] = {}

    # ── Security: GoPlus / token_security checks + rug-pattern matches ──
    sec: dict[str, Any] = {}
    if r.security and isinstance(r.security, dict):
        checks = r.security.get("checks")
        if isinstance(checks, dict):
            sec["checks"] = {
                cid: {"status": c.get("status", ""), "details": c.get("details", "")} for cid, c in checks.items()
            }
        for key in ("score", "band", "category_breakdown"):
            if key in r.security:
                sec[key] = r.security[key]
    if r.rug_patterns and isinstance(r.rug_patterns, dict):
        sec["rug_patterns"] = {
            "match_count": r.rug_patterns.get("match_count", 0),
            "patterns": r.rug_patterns.get("patterns") or r.rug_patterns.get("matches", []),
        }
    if sec:
        ev["security"] = sec

    # ── Liquidity ──
    if r.liquidity and isinstance(r.liquidity, dict):
        ev["liquidity"] = {
            "locked_pct": r.liquidity.get("locked_pct"),
            "liquidity_usd": r.liquidity.get("liquidity_usd", r.liquidity.get("total_liquidity")),
        }

    # ── Holders ──
    if r.holders and isinstance(r.holders, dict):
        ev["holders"] = {
            "top_10_pct": r.holders.get("top_10_pct"),
            "holder_count": r.holders.get("holder_count"),
        }
    if r.holder_health and isinstance(r.holder_health, dict):
        ev["holder_health"] = {
            "gini": r.holder_health.get("gini"),
            "hhi": r.holder_health.get("hhi"),
        }
    if r.funding_analysis and isinstance(r.funding_analysis, dict):
        ev["funding_analysis"] = {
            "exchange_funded_pct": r.funding_analysis.get("exchange_funded_pct"),
            "fresh_wallet_pct": r.funding_analysis.get("fresh_wallet_pct"),
            "mixer_funded_pct": r.funding_analysis.get("mixer_funded_pct"),
            "risk_assessment": r.funding_analysis.get("risk_assessment"),
            "top_funders": r.funding_analysis.get("top_funders"),
        }
    if r.wallet_freshness and isinstance(r.wallet_freshness, dict):
        ev["wallet_freshness"] = {
            "fresh_wallet_pct": r.wallet_freshness.get("fresh_wallet_pct"),
            "avg_freshness_score": r.wallet_freshness.get("avg_freshness_score"),
            "analyzed": r.wallet_freshness.get("analyzed"),
        }
    if r.holder_overlap and isinstance(r.holder_overlap, dict):
        ev["holder_overlap"] = {
            "max_overlap_pct": r.holder_overlap.get("max_overlap_pct"),
            "worst_token": r.holder_overlap.get("worst_token"),
            "worst_token_is_scam": r.holder_overlap.get("worst_token_is_scam"),
            "risk_assessment": r.holder_overlap.get("risk_assessment"),
        }

    # ── Deployer ──
    if r.dev_reputation and isinstance(r.dev_reputation, dict):
        ev["deployer"] = {
            "deployer_address": r.dev_reputation.get("deployer_address") or r.dev_reputation.get("address"),
            "risk_score": r.dev_reputation.get("risk_score"),
            "risk_label": r.dev_reputation.get("risk_label") or r.dev_reputation.get("reputation_label"),
            "rug_count": r.dev_reputation.get("rug_count"),
            "total_deployments": r.dev_reputation.get("total_deployments") or r.dev_reputation.get("deployment_count"),
        }

    # ── Market ──
    mkt: dict[str, Any] = {}
    if r.volume_auth and isinstance(r.volume_auth, dict):
        # The volume-authenticity engine's full wash payload — not just the
        # fake-volume percentage. The report formatter renders the dedicated
        # 📉 Fake Volume verdict line from these (self-deal overlap,
        # volume-to-holder churn, organic/likely verdict).
        mkt["fake_volume_pct"] = r.volume_auth.get("fake_volume_pct")
        mkt["authentic_score"] = r.volume_auth.get("authentic_score")
        mkt["risk_level"] = r.volume_auth.get("risk_level")
        mkt["self_deal_wallets"] = r.volume_auth.get("self_deal_wallets")
        mkt["self_deal_swaps"] = r.volume_auth.get("self_deal_swaps")
        mkt["self_deal_swap_ratio"] = r.volume_auth.get("self_deal_swap_ratio")
        mkt["active_holders"] = r.volume_auth.get("active_holders")
        mkt["volume_to_holder_ratio"] = r.volume_auth.get("volume_to_holder_ratio")
        mkt["wash_signals"] = r.volume_auth.get("wash_signals")
        mkt["fake_volume_verdict"] = r.volume_auth.get("fake_volume_verdict")
        mkt["fake_volume_detail"] = r.volume_auth.get("fake_volume_detail")
        mkt["confidence"] = r.volume_auth.get("confidence")
    if r.premium and isinstance(r.premium, dict):
        mkt["premium"] = {
            feature: {"risk": d.get("risk", d.get("risk_level", "")) if isinstance(d, dict) else d}
            for feature, d in r.premium.items()
            if isinstance(d, dict) and d
        }
    if r.lite_chain_indexer_patterns and isinstance(r.lite_chain_indexer_patterns, dict):
        findings = r.lite_chain_indexer_patterns.get("findings")
        if findings is None:
            data = r.lite_chain_indexer_patterns.get("data")
            findings = data.get("findings") if isinstance(data, dict) else None
        mkt["lite_chain_indexer"] = findings or []
    if mkt:
        ev["market"] = mkt

    # ── SENTINEL deep-forensic findings (Phase 5) ──
    if r.sentinel and isinstance(r.sentinel, dict):
        sfree = r.sentinel.get("free") or {}
        sentinel_ev: dict[str, Any] = {}

        ha = sfree.get("holder_analysis")
        if isinstance(ha, dict) and ha.get("hhi") is not None:
            sentinel_ev["holder_analysis"] = {
                "hhi": ha.get("hhi"),
                "risk_level": ha.get("risk_level"),
                "fake_diversification_score": ha.get("fake_diversification_score"),
                "total_holders": ha.get("total_holders"),
            }
        lv = sfree.get("liquidity_verification")
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            sentinel_ev["liquidity_verification"] = {
                "total_locked_percentage": lv.get("total_locked_percentage"),
                "risk_level": lv.get("risk_level"),
                "fake_lock_detected": lv.get("fake_lock_detected"),
                "lp_burned": lv.get("lp_burned"),
            }
        dr = sfree.get("dev_reputation")
        if isinstance(dr, dict) and dr.get("reputation_score") is not None:
            sentinel_ev["dev_reputation"] = {
                "reputation_score": dr.get("reputation_score"),
                "reputation_level": dr.get("reputation_level"),
                "dev_threat_index": dr.get("dev_threat_index"),
                "rug_rate": dr.get("rug_rate"),
                "serial_patterns": dr.get("serial_patterns"),
            }
        ca = sfree.get("contract_authority")
        if isinstance(ca, dict) and ca.get("risk_level"):
            sentinel_ev["contract_authority"] = {
                "risk_score": ca.get("risk_score"),
                "risk_level": ca.get("risk_level"),
            }
        if sentinel_ev:
            ev["sentinel"] = sentinel_ev
        if r.sentinel_boost:
            sentinel_ev["sentinel_boost"] = r.sentinel_boost

    # ── Market context (Part 1 — market-aware scanning) ──
    if r.market_context is not None:
        ev["market_context"] = r.market_context.to_dict()

    return ev
