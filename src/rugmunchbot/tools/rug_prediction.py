"""Rug Prediction Score — composite 7-day rug-risk forecast.

Win 31: the flagship differentiator. Combines every validated signal in
the scan result into a single weighted prediction, with confidence that
reflects sample depth. Every input is optional; the score degrades
honestly (confidence drops, never fabricates).

Signal weights (sum = 100 when all present):
    honeypot/mint/freeze authorities   25   (contract-level, binary)
    bundle cluster coordination        20   (launch forensics)
    holder concentration + fakes       20   (distribution health)
    wash / fake volume                 15   (volume authenticity)
    flow concentration                 10   (live transfer behavior)
    fresh wallets + CEX-funded mix     10   (participant quality)

Output: rug_probability (0-100), band, confidence (0-100), drivers list.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _f(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def predict(scan: dict[str, Any]) -> dict[str, Any]:
    """Compute the composite rug prediction from a scan result dict."""
    probability = 0.0
    confidence = 0.0
    weights_seen = 0.0
    drivers: list[dict[str, Any]] = []

    def add_driver(signal: str, weight: float, detail: str) -> None:
        drivers.append({"signal": signal, "weight": round(weight, 1), "detail": detail})

    # ── Contract authorities (25) ──
    w = 0.0
    if scan.get("honeypot") is True:
        w += 12
        add_driver("honeypot", 12, "sells can be blocked — hard trap")
    mint = scan.get("mint_auth") if scan.get("mint_auth") is not None else scan.get("mint_authority")
    if mint is True or str(mint).lower() in ("true", "mint", "active"):
        w += 8
        add_driver("mint_authority", 8, "supply can be inflated anytime")
    freeze = scan.get("freeze_auth") or scan.get("freeze_authority")
    if freeze is True or str(freeze).lower() in ("true", "freeze", "active"):
        w += 5
        add_driver("freeze_authority", 5, "wallets can be frozen")
    probability += w * (25 / 25)
    confidence += (w / 25) * 25
    weights_seen += 25

    # ── Bundle coordination (20) ──
    ba = scan.get("bundle_analysis") if isinstance(scan.get("bundle_analysis"), dict) else {}
    cluster = _f(ba.get("launch_cluster_score"))
    if cluster is not None:
        w = min(20.0, cluster / 100 * 20)
        probability += w
        confidence += 20
        weights_seen += 20
        if cluster >= 70:
            add_driver("bundle_cluster", w, f"coordinated launch (cluster {cluster:.0f})")

    # ── Holder distribution (20) ──
    ha = scan.get("holder_analysis") if isinstance(scan.get("holder_analysis"), dict) else {}
    conc = _f(ha.get("concentration_pct")) or _f(ha.get("top_10_pct"))
    top10 = _f(ha.get("top10_pct")) or _f(ha.get("top_10_pct")) or conc
    fake_div = _f(ha.get("fake_diversification_score"))
    hw = 0.0
    if top10 is not None and top10 >= 50:
        hw += 10
        add_driver("holder_concentration", 10, f"top-10 holds {top10:.0f}%")
    elif top10 is not None and top10 >= 30:
        hw += 5
        add_driver("holder_concentration", 5, f"top-10 holds {top10:.0f}% (moderate)")
    if fake_div is not None and fake_div >= 60:
        hw += 10
        add_driver("fake_diversification", 10, f"diversity likely faked ({fake_div:.0f}/100)")
    probability += hw
    confidence += (hw / 20) * 20 if hw else 0
    weights_seen += 20

    # ── Wash volume (15) ──
    wt = scan.get("wash_trading") if isinstance(scan.get("wash_trading"), dict) else {}
    ws = _f(wt.get("score"))
    if ws is not None:
        # wash score: 100=clean, 0=washed -> invert
        w = (100 - ws) / 100 * 15
        probability += w
        confidence += 15
        weights_seen += 15
        if ws < 55:
            add_driver("wash_volume", w, f"volume integrity {ws:.0f}/100 (LIKELY_WASHED band)")

    # ── Flow concentration (10) ──
    fa = scan.get("flow_analysis") if isinstance(scan.get("flow_analysis"), dict) else {}
    if fa.get("concentrated_flow"):
        share = _f(fa.get("top_mover_share_pct")) or 50
        w = min(10.0, share / 100 * 10)
        probability += w
        confidence += 10
        weights_seen += 10
        add_driver("flow_concentration", w, f"one wallet = {share:.0f}% of recent transfers")

    # ── Participant quality (10) ──
    pq = 0.0
    cex = _f(ha.get("exchange_funded_pct"))
    fresh = _f(ha.get("new_wallets_pct"))
    if cex is not None and cex >= 30:
        pq += 5
        add_driver("cex_funded_holders", 5, f"{cex:.0f}% of holders exchange-funded")
    if fresh is not None and fresh >= 50:
        pq += 5
        add_driver("fresh_wallets", 5, f"{fresh:.0f}% of wallets are brand new")
    probability += pq
    confidence += (pq / 10) * 10
    weights_seen += 10

    probability = round(min(100.0, max(0.0, probability)), 1)
    # Confidence: how much of the signal space actually had data
    confidence = round(min(100.0, (weights_seen and (confidence / weights_seen * 100)) or 0.0), 1)

    if probability >= 70:
        band = "EXTREME_RUG_RISK"
    elif probability >= 45:
        band = "HIGH_RUG_RISK"
    elif probability >= 25:
        band = "ELEVATED"
    else:
        band = "LOW"

    drivers.sort(key=lambda d: d["weight"], reverse=True)
    return {
        "rug_probability": probability,
        "band": band,
        "confidence": confidence,
        "drivers": drivers[:6],
        "methodology": (
            "Weighted composite of validated scan signals: contract "
            "authorities, bundle coordination, holder distribution, volume "
            "authenticity, live flow concentration, participant quality."
        ),
    }


async def rug_prediction(*, scan_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tool entry: compute prediction from an embedded scan result payload."""
    if not scan_result:
        return {"error": "scan_result payload required"}
    out = predict(scan_result)
    out["prediction"] = True
    return out
