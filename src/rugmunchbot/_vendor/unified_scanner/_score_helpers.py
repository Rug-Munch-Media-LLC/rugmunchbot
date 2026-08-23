# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/unified/_score_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pure scoring helpers — SENTINEL risk-dimension pass + per-dimension breakdown.

Extracted from ``unified/scoring.py`` (degodding wave 4, 2026-08-08) so the
two largest mechanical blocks inside ``compute_score`` (F-110+) live beside
the other pure scoring helpers instead of inflating the orchestrator:

- ``apply_sentinel_scoring`` — the SENTINEL deep-forensic (Phase 5) pass that
  feeds ``holder_analysis`` / ``liquidity_verification`` / ``dev_reputation`` /
  ``contract_authority`` / ``proxy_detection`` / ``metadata_fingerprint`` into
  the five risk dimensions via a caller-supplied ``apply`` callback, and
  returns the accumulated confidence delta.
- ``build_breakdown`` — the explainable per-dimension breakdown payload
  (``score`` / ``max`` / ``delta`` / ``flags``) from the surviving deltas +
  flags, used at the end of ``compute_score``.

Both are pure with respect to the score state: all mutation flows through the
caller-supplied ``apply`` callback or the returned value. Never raises.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from rugmunchbot._vendor.unified_scanner.model import ScanResult


def apply_sentinel_scoring(
    r: ScanResult,
    *,
    bluechip: bool,
    apply: Callable[[str, float, str | None], None],
) -> float:
    """Apply SENTINEL deep-forensic findings (Phase 5) to the five dimensions.

    SENTINEL free-tier modules report alongside the DataBus chains; their
    findings feed the SAME five dimensions so a critical deep-forensic signal
    moves the unified score (not just the SENTINEL report). A
    ``sentinel_boost`` is applied when two or more modules independently flag
    critical/high risk — correlated deep-forensic signals reinforce each other.

    Args:
        r: The scan result being scored (``r.sentinel["free"]`` read only).
        bluechip: Blue-chip guard — skip concentration/lock heuristics that
            are structurally normal for wrapped/blue-chip tokens (Bug 1).
        apply: Callback ``apply(dim, delta, flag)`` recording a signed delta
            against a dimension (and the flag, when given).

    Returns:
        The confidence points accumulated by the sentinel pass (0.0 when no
        sentinel findings are present).
    """
    sentinel = r.sentinel
    sentinel_free = (sentinel.get("free") or {}) if isinstance(sentinel, dict) else {}
    sentinel_critical: list[str] = []
    confidence = 0.0

    # holder_analysis (HHIReport) → holders dimension
    ha = sentinel_free.get("holder_analysis")
    if isinstance(ha, dict) and ha.get("hhi") is not None:
        hhi = float(ha.get("hhi", 0) or 0)
        ha_risk = str(ha.get("risk_level", "")).lower()
        # HHI > 5000 is normal for wrapped/blue-chip tokens (one
        # wrapping contract dominates supply) — skip the SENTINEL HHI
        # flags for bluechips (Bug 1).
        if not bluechip and (hhi > 5000 or ha_risk == "critical"):
            apply("holders", -10, "SENTINEL_HHI_CRITICAL")
            sentinel_critical.append("holder_analysis")
        elif not bluechip and (hhi > 3000 or ha_risk == "high"):
            apply("holders", -5, "SENTINEL_HHI_HIGH")
        fake_div = float(ha.get("fake_diversification_score", 0) or 0)
        if fake_div > 40:
            apply("holders", -5, "SENTINEL_FAKE_DIVERSIFICATION")
        if ha.get("total_holders") is not None:
            confidence += 5

    # liquidity_verification (LockReport) → liquidity dimension
    lv = sentinel_free.get("liquidity_verification")
    if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
        locked = float(lv.get("total_locked_percentage", 0) or 0)
        lv_risk = str(lv.get("risk_level", "")).upper()
        fake_lock = bool(lv.get("fake_lock_detected"))
        # LP lock < 50% is normal for wrapped/blue-chip tokens (deep
        # AMM pools instead of locked LP positions) — skip the lock
        # penalty for bluechips unless a real fake-lock/CRITICAL
        # finding exists (Bug 1).
        if (not bluechip and locked < 50) or lv_risk == "CRITICAL" or fake_lock:
            apply("liquidity", -10, "SENTINEL_LP_LOCK")
            if lv_risk == "CRITICAL" or fake_lock:
                sentinel_critical.append("liquidity_verification")
        elif locked > 90:
            apply("liquidity", 3, "SENTINEL_LP_LOCKED")
        if lv.get("warnings"):
            confidence += 3

    # dev_reputation (DevReputationReport) → deployer dimension
    dr = sentinel_free.get("dev_reputation")
    if isinstance(dr, dict) and dr.get("reputation_score") is not None:
        rep_score = float(dr.get("reputation_score", 50) or 50)
        threat = float(dr.get("dev_threat_index", 0) or 0)
        if rep_score < 30 or threat > 0.5:
            apply("deployer", -10, "SENTINEL_DEV_THREAT")
            sentinel_critical.append("dev_reputation")
        elif rep_score >= 80:
            apply("deployer", 3, "SENTINEL_DEV_CLEAN")
        if dr.get("total_launches") is not None:
            confidence += 5

    # contract_authority / proxy_detection / metadata_fingerprint → security dimension
    ca = sentinel_free.get("contract_authority")
    if isinstance(ca, dict) and str(ca.get("risk_level", "")).upper() == "CRITICAL":
        apply("security", -5, "SENTINEL_CONTRACT_AUTHORITY")
        sentinel_critical.append("contract_authority")
    px = sentinel_free.get("proxy_detection")
    if isinstance(px, dict) and (
        str(px.get("risk_level", "")).upper() == "CRITICAL" or (px.get("can_upgrade") and px.get("is_proxy"))
    ):
        apply("security", -5, "SENTINEL_PROXY_RISK")
        sentinel_critical.append("proxy_detection")
    mf = sentinel_free.get("metadata_fingerprint")
    if isinstance(mf, dict) and len(mf.get("similar_tokens") or []) >= 3:
        apply("security", -5, "SENTINEL_METADATA_CLONE")

    # sentinel_boost — correlated critical deep-forensic signals reinforce.
    if len(sentinel_critical) >= 2:
        boost = min(15.0, 5.0 * len(sentinel_critical))
        apply("security", -boost, f"SENTINEL_CRITICAL_BOOST:{len(sentinel_critical)}")
        r.sentinel_boost = boost

    return confidence


def build_breakdown(
    deltas: dict[str, float],
    dim_flags: dict[str, list[str]],
    dimension_weights: dict[str, int],
) -> dict[str, Any]:
    """Build the explainable per-dimension breakdown payload.

    Pure mapping: for each dimension weight, clamp ``weight + delta`` into
    ``[0, weight]`` as the surviving ``score`` and attach the flags that
    caused the delta. ``score = clamp(0, max, max + delta)``.
    """
    breakdown: dict[str, Any] = {}
    for dim, weight in dimension_weights.items():
        delta = deltas.get(dim, 0.0)
        breakdown[dim] = {
            "score": round(max(0.0, min(float(weight), float(weight) + delta)), 1),
            "max": weight,
            "delta": round(delta, 1),
            "flags": dim_flags.get(dim, []),
        }
    return breakdown
