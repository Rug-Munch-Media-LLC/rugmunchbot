# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/_own_bundle_scoring.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Pure scoring for own_bundle_detect (degodding split 2026-08-13).

All ``_score_*`` / ``_compute_*`` functions from ``own_bundle_detect.py``
live here. They are pure logic over already-fetched query rows (blocks,
funders, holder concentration, launch cluster) — no ClickHouse or Redis
access. ``own_bundle_detect.py`` re-exports every name so
``bd._score_atomic_blocks`` etc. keep resolving in tests
(test_own_bundle_detect, test_scan_enhancements).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.scanner_engines._own_bundle_common import _SIMILAR_AMOUNT_CV_BANDS


def _score_atomic_blocks(
    result: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> None:
    """Score based on atomic block buys."""
    if not blocks:
        return

    result["bundle_blocks"] = blocks

    # Find the highest buyer count in a single block
    max_buyers = max(b.get("buyer_count", 0) for b in blocks)

    # Score: 0-100 based on max buyers in one block
    if max_buyers >= 20:
        result["atomic_block_score"] = 100
    elif max_buyers >= 10:
        result["atomic_block_score"] = 80
    elif max_buyers >= 7:
        result["atomic_block_score"] = 60
    elif max_buyers >= 5:
        result["atomic_block_score"] = 40
    elif max_buyers >= 3:
        result["atomic_block_score"] = 20

    # Bonus for multiple high-buyer blocks
    high_buyer_blocks = sum(1 for b in blocks if b.get("buyer_count", 0) >= 5)
    if high_buyer_blocks >= 3:
        result["atomic_block_score"] = min(100, result["atomic_block_score"] + 15)


def _score_common_funders(
    result: dict[str, Any],
    funders: list[dict[str, Any]],
) -> None:
    """Score based on common funding sources."""
    if not funders:
        return

    result["common_funders"] = funders

    # Max funded count
    max_funded = max(f.get("funded_count", 0) for f in funders)

    # Score based on how many buyers share a funder
    if max_funded >= 10:
        result["common_funder_score"] = 100
    elif max_funded >= 5:
        result["common_funder_score"] = 70
    elif max_funded >= 3:
        result["common_funder_score"] = 40
    elif max_funded >= 2:
        result["common_funder_score"] = 20

    # Bonus for multiple funders each funding multiple wallets
    multi_funders = sum(1 for f in funders if f.get("funded_count", 0) >= 3)
    if multi_funders >= 3:
        result["common_funder_score"] = min(100, result["common_funder_score"] + 10)


def _score_holder_concentration(
    result: dict[str, Any],
    holder_data: dict[str, Any],
) -> None:
    """Score based on holder concentration."""
    result["top3_holder_pct"] = holder_data.get("top3_pct", 0)
    result["top10_holder_pct"] = holder_data.get("top10_pct", 0)
    result["holder_count"] = holder_data.get("holder_count", 0)

    top3 = result["top3_holder_pct"]
    top10 = result["top10_holder_pct"]

    if top3 > 90:
        result["distribution_anomaly_score"] = 100
        result["concentration_score"] = 100
    elif top3 > 80:
        result["distribution_anomaly_score"] = 80
        result["concentration_score"] = 80
    elif top3 > 60:
        result["distribution_anomaly_score"] = 60
        result["concentration_score"] = 60
    elif top10 > 90:
        result["distribution_anomaly_score"] = 50
        result["concentration_score"] = 50
    elif top3 > 40:
        result["distribution_anomaly_score"] = 30
        result["concentration_score"] = 30
    elif top10 > 70:
        result["distribution_anomaly_score"] = 20
        result["concentration_score"] = 20


def _score_launch_cluster(
    result: dict[str, Any],
    cluster: dict[str, Any],
) -> None:
    """Score a launch-window buy cluster for coordinated bundling.

    Pure logic over ``_query_launch_cluster`` output. Signals (each 0-100):

      - ``launch_cluster_score`` — distinct wallets buying in the same launch
        block (density, 60%) + share of first-time buyers (40%).
      - ``similar_amount_score`` — low coefficient of variation among launch
        buy amounts (bot-sized buys cluster at similar amounts).
      - ``deployer_funded_pct`` — share of launch buyers funded directly by
        the deployer (same-funder signal).
    """
    launch_buyers = cluster.get("launch_buyers") or []
    cluster_blocks = cluster.get("cluster_blocks") or []
    deploy_block = cluster.get("deploy_block")

    result["deploy_block"] = deploy_block if deploy_block else None
    result["cluster_blocks"] = cluster_blocks

    if not launch_buyers:
        return

    result["launch_buyers"] = len(launch_buyers)

    # (a) + (d) — first-time buyers with no prior token history
    first_tx_count = sum(1 for b in launch_buyers if b.get("is_first_tx"))
    first_tx_pct = first_tx_count / len(launch_buyers) * 100
    result["first_time_buyer_pct"] = round(first_tx_pct, 1)

    # Cluster density: max distinct buyers in a single launch block.
    max_block_buyers = max((int(b.get("buyer_count", 0)) for b in cluster_blocks), default=0)
    density_score = min(100.0, max_block_buyers * 10.0)

    first_tx_score = min(100.0, first_tx_pct)
    result["launch_cluster_score"] = round(0.6 * density_score + 0.4 * first_tx_score, 1)

    # (c) — similar purchase amounts (low CV among positive amounts)
    amounts = [float(b.get("amount", 0) or 0) for b in launch_buyers if float(b.get("amount", 0) or 0) > 0]
    if len(amounts) >= 2:
        mean = sum(amounts) / len(amounts)
        if mean > 0:
            variance = sum((a - mean) ** 2 for a in amounts) / len(amounts)
            std = variance**0.5
            cv = std / mean
            similar = 0.0
            for max_cv, score in _SIMILAR_AMOUNT_CV_BANDS:
                if cv <= max_cv:
                    similar = score
                    break
            result["similar_amount_score"] = similar

    # (b) — same-funder signal: share funded directly by the deployer
    deployer_count = sum(1 for b in launch_buyers if b.get("deployer_funded"))
    result["deployer_funded_pct"] = round(deployer_count / len(launch_buyers) * 100, 1)


def _compute_launch_bundle_verdict(result: dict[str, Any]) -> None:
    """Aggregate launch-window signals into ``is_bundled`` / ``confidence``.

    Used by ``detect_bundled_launch``. Weights:
      - launch_cluster_score (density + first-time buyers): 0.40
      - similar_amount_score (bot-sized buys): 0.20
      - common_funder_score (shared funder among buyers): 0.25
      - deployer_funded_pct (deployer-funded wallets): 0.15
    """
    launch = float(result.get("launch_cluster_score", 0) or 0)
    similar = float(result.get("similar_amount_score", 0) or 0)
    funder = float(result.get("common_funder_score", 0) or 0)
    deployer = float(result.get("deployer_funded_pct", 0) or 0)

    aggregated = launch * 0.40 + similar * 0.20 + funder * 0.25 + min(deployer, 100.0) * 0.15
    result["confidence"] = round(aggregated, 1)

    if aggregated >= 60:
        result["is_bundled"] = True
        result["risk_label"] = "critical"
    elif aggregated >= 40:
        result["is_bundled"] = True
        result["risk_label"] = "high"
    elif aggregated >= 25:
        result["is_bundled"] = True
        result["risk_label"] = "medium"
    else:
        result["is_bundled"] = False
        result["risk_label"] = "low"


def _compute_bundle_score(result: dict[str, Any]) -> None:
    """Compute aggregate bundle score from sub-scores.

    The launch-cluster signal (when present) contributes a capped bonus of
    ``launch_cluster_score * 0.10`` — the base weights are unchanged so
    existing behavior is preserved when no launch data is available.
    """
    atomic = result.get("atomic_block_score", 0) or 0
    funder = result.get("common_funder_score", 0) or 0
    distribution = result.get("distribution_anomaly_score", 0) or 0
    concentration = result.get("concentration_score", 0) or 0
    launch = result.get("launch_cluster_score", 0) or 0

    # Weighted combination
    scores = [
        (atomic, 0.35),  # Atomic block buys: strongest signal
        (funder, 0.25),  # Common funders: strong signal
        (distribution, 0.25),  # Distribution anomaly: moderate
        (concentration, 0.15),  # Concentration: supporting
    ]

    aggregated = sum(s * w for s, w in scores)
    # Launch-cluster bonus (0-10): a clear launch bundle (multiple first-time
    # buyers in the first blocks) pushes a borderline aggregate over the line.
    aggregated = aggregated + (launch * 0.10)
    result["confidence"] = round(aggregated, 1)

    # Determine bundled status
    if aggregated >= 70:
        result["is_bundled"] = True
        result["risk_label"] = "critical"
    elif aggregated >= 50:
        result["is_bundled"] = True
        result["risk_label"] = "high"
    elif aggregated >= 30:
        result["is_bundled"] = True
        result["risk_label"] = "medium"
    else:
        result["risk_label"] = "low"
