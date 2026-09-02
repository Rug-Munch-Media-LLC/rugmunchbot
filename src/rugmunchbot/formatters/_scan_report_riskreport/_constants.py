"""Canonical constants + local coercion helpers for the RiskReport section.

Split out of ``formatters/_scan_report_riskreport.py`` during the de-godding
pass. This module holds the canonical mirror tables (factor keys / labels /
weights / band thresholds) copied verbatim from
``rugmunch_scanner/unified/risk_report.py`` plus the small pure coercion
helpers shared across the package. Pure module: no network, no I/O.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import _float_or_none

# ═══════════════════════════════════════════════════════════════════════════
# CANONICAL MIRROR — keep in sync with rugmunch_scanner/unified/risk_report.py
# ═══════════════════════════════════════════════════════════════════════════
# Factor keys, labels, weights and band thresholds below are copied verbatim
# from the scanner's RiskReport module so a bot-built report scores and
# renders identically to a backend-provided one. When the canonical file
# changes, update BOTH tables and the band constants.

_CRITICAL_MIN = 70.0  # factor score >= 70 → CRITICAL
_RISK_MIN = 25.0  # factor score >= 25 → RISK (else OK)

_SCAM_MIN = 85.0  # composite >= 85 → scam (or confirmed honeypot)
_RUG_MIN = 70.0  # composite >= 70 → rug (or rugged-dev + bundle)
_RISKY_MIN = 45.0  # composite >= 45 → risky
_MIN_KNOWN_FOR_DETERMINATION = 6  # need >= 6 known factors to declare a verdict

_FACTOR_LABELS: dict[str, str] = {
    "deployer_repeat": "Repeat deployer",
    "deployer_rugged_before": "Deployer previously rugged",
    "deployer_new_wallet": "Fresh deployer wallet",
    "deployer_funding": "Deployer funding source",
    "holder_concentration": "Holder concentration (top-10)",
    "dev_team_share": "Dev/team wallet share",
    "top_holder_whale": "Whale top holder",
    "fresh_wallet_pct": "Fresh wallet share",
    "cex_funded_wallet_pct": "CEX-funded wallet share",
    "first_buyer_deployer": "First buyer is deployer",
    "first_buyer_bundle": "First buyer in bundle",
    "bundle_launch": "Bundled launch",
    "sniper_first_buyers": "Sniper first-buyers",
    "funder_cluster": "Shared-funder cluster",
    "cross_token_cluster": "Cross-token wallet cluster",
    "shared_deployer_network": "Shared-deployer network",
    "cross_token_funder": "Cross-token funder graph",
    "temporal_cluster": "Lockstep entry (temporal) cluster",
    "sniper_bot_pattern": "Sniper/bot launch pattern",
    "mev_bundle": "MEV bundle buys",
    "wash_trading_cluster": "Wash-trading cluster",
    "honeypot": "Honeypot (cannot sell)",
    "mint_authority": "Mint authority active",
    "freeze_authority": "Freeze authority active",
    "ownership_renounced": "Ownership not renounced",
    "lp_locked": "Liquidity locked",
    "lp_liquidity_amount": "Liquidity amount",
    "buy_sell_tax": "Buy/sell tax",
    "volume_authenticity": "Volume authenticity",
    "holder_overlap_scam": "Holder overlap with known scams",
}

_FACTOR_WEIGHTS: dict[str, float] = {
    "deployer_repeat": 2.0,
    "deployer_rugged_before": 3.0,
    "deployer_new_wallet": 1.5,
    "deployer_funding": 1.5,
    "holder_concentration": 2.5,
    "dev_team_share": 2.5,
    "top_holder_whale": 1.5,
    "fresh_wallet_pct": 2.0,
    "cex_funded_wallet_pct": 1.0,
    "first_buyer_deployer": 1.5,
    "first_buyer_bundle": 2.0,
    "bundle_launch": 3.0,
    "sniper_first_buyers": 1.5,
    "funder_cluster": 2.5,
    "cross_token_cluster": 2.0,
    "shared_deployer_network": 2.5,
    "cross_token_funder": 2.0,
    "temporal_cluster": 2.0,
    "sniper_bot_pattern": 2.0,
    "mev_bundle": 2.5,
    "wash_trading_cluster": 2.5,
    "honeypot": 5.0,
    "mint_authority": 3.0,
    "freeze_authority": 2.0,
    "ownership_renounced": 2.0,
    "lp_locked": 3.0,
    "lp_liquidity_amount": 1.5,
    "buy_sell_tax": 1.5,
    "volume_authenticity": 1.5,
    "holder_overlap_scam": 2.0,
}

# ═══════════════════════════════════════════════════════════════════════════
# LOCAL COERCION HELPERS
# ═══════════════════════════════════════════════════════════════════════════

_MIDDOT = " \u00b7 "  # interpunct separator used in section one-liners
# ═══════════════════════════════════════════════════════════════════════════


def _f(value: Any) -> float | None:
    """Coerce to float; None for missing/NaN/blank values (never raises)."""
    return _float_or_none(value)


def _i(value: Any) -> int:
    """Coerce to int (0 when missing/unparseable)."""
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _sec(r: dict[str, Any]) -> dict[str, Any]:
    """Backend security evidence (GoPlus-style checks) or {}."""
    ev = r.get("evidence")
    s = ev.get("security") if isinstance(ev, dict) else None
    return s if isinstance(s, dict) else {}


def _sentinel_module(r: dict[str, Any], key: str) -> dict[str, Any]:
    """Pull one SENTINEL module result (free/premium buckets) defensively."""
    sent = r.get("sentinel")
    if not isinstance(sent, dict):
        return {}
    for bucket in ("free", "premium"):
        mods = sent.get(bucket)
        if isinstance(mods, dict):
            res = mods.get(key)
            if isinstance(res, dict):
                return res
    return {}


def _first_buyer_dict(r: dict[str, Any]) -> dict[str, Any]:
    """First-buyer entry from the own indexer engine, or {}.

    Accepts both payload shapes: the legacy list (``[{address, ...}]``) and
    the 250-wallet cohort dict (``{buyers: [...], aggregates: {...}, limit}``
    emitted by the deep endpoint / scanner engine). For the cohort shape the
    earliest buyer is the first ``buyers`` entry and the aggregates ride
    along so the RiskReport first-buyer section can report cohort depth.
    """
    fb = r.get("first_buyers")
    if isinstance(fb, list) and fb:
        first = fb[0]
        return first if isinstance(first, dict) else {}
    if isinstance(fb, dict):
        buyers = fb.get("buyers")
        if isinstance(buyers, list) and buyers:
            first = buyers[0]
            if isinstance(first, dict):
                return {
                    **first,
                    "cohort_size": (fb.get("aggregates") or {}).get("unique_buyers"),
                    "cohort_limit": fb.get("limit"),
                    "cohort_fresh_pct": (fb.get("aggregates") or {}).get("fresh_wallet_pct"),
                    "cohort_bundled_pct": (fb.get("aggregates") or {}).get("bundled_pct"),
                }
        return fb
    return {}


def _short(value: Any) -> str:
    """Shorten an address for display ("" for missing)."""
    if not value:
        return ""
    s = str(value)
    return s if len(s) <= 14 else f"{s[:10]}\u2026{s[-4:]}"
