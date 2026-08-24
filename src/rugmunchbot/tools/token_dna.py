"""Token DNA Fingerprint — behavioral similarity matching vs known rugs.

NOBODY else does this. Instead of just scoring risk factors, we create a
multi-dimensional behavioral fingerprint of a token and compare it against
our database of known rug patterns. Like genetic sequencing for tokens.

The DNA vector captures 8 behavioral dimensions:
    1. holder_concentration   (top-10 % of supply)
    2. wash_intensity         (volume authenticity inverse)
    3. bundle_coordination    (launch cluster score)
    4. fresh_wallet_ratio     (% brand-new wallets)
    5. cex_dependency         (% holders from exchange wallets)
    6. flow_concentration     (top mover share of recent transfers)
    7. liquidity_fragility    (100 - LP locked %)
    8. authority_risk         (mint + freeze + owner flags)

Similarity = cosine distance between the token's DNA vector and known
rug-pattern centroids. We maintain three centroids:
    SOFT_RUG: LP pull / gradual dump (high liquidity fragility + flow conc)
    HARD_RUG: honeypot / mint abuse (high authority risk + concentration)
    VOLUME_FRAUD: wash trading (high wash intensity + low holder count)

The match tells users WHAT KIND of rug this looks like, not just "risky."
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# Known rug-pattern centroids (normalized 0-1 per dimension).
# Derived from historical rug analysis; recalibrate as data grows.
RUG_CENTROIDS: dict[str, dict[str, float]] = {
    "SOFT_RUG": {
        "holder_concentration": 0.55,
        "wash_intensity": 0.45,
        "bundle_coordination": 0.40,
        "fresh_wallet_ratio": 0.60,
        "cex_dependency": 0.30,
        "flow_concentration": 0.70,
        "liquidity_fragility": 0.85,
        "authority_risk": 0.35,
    },
    "HARD_RUG": {
        "holder_concentration": 0.85,
        "wash_intensity": 0.35,
        "bundle_coordination": 0.75,
        "fresh_wallet_ratio": 0.70,
        "cex_dependency": 0.20,
        "flow_concentration": 0.50,
        "liquidity_fragility": 0.40,
        "authority_risk": 0.90,
    },
    "VOLUME_FRAUD": {
        "holder_concentration": 0.35,
        "wash_intensity": 0.90,
        "bundle_coordination": 0.30,
        "fresh_wallet_ratio": 0.45,
        "cex_dependency": 0.25,
        "flow_concentration": 0.60,
        "liquidity_fragility": 0.30,
        "authority_risk": 0.25,
    },
}

DIMENSIONS = list(next(iter(RUG_CENTROIDS.values())).keys())


def _f(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def extract_dna(scan: dict[str, Any]) -> dict[str, float] | None:
    """Extract the 8-dimensional behavioral fingerprint from a scan result.

    Returns None when insufficient data (fewer than 3 dimensions available).
    """
    ha = scan.get("holder_analysis") if isinstance(scan.get("holder_analysis"), dict) else {}
    wt = scan.get("wash_trading") if isinstance(scan.get("wash_trading"), dict) else {}
    ba = scan.get("bundle_analysis") if isinstance(scan.get("bundle_analysis"), dict) else {}
    fa = scan.get("flow_analysis") if isinstance(scan.get("flow_analysis"), dict) else {}

    dims: dict[str, float] = {}

    # 1. Holder concentration (top10_pct → 0-1)
    v = _f(ha.get("top_10_pct")) or _f(ha.get("top10_pct"))
    if v is not None:
        dims["holder_concentration"] = min(v / 100, 1.0)

    # 2. Wash intensity (inverse of volume integrity score)
    v = _f(wt.get("score"))
    if v is not None:
        dims["wash_intensity"] = min((100 - v) / 100, 1.0)

    # 3. Bundle coordination
    v = _f(ba.get("launch_cluster_score"))
    if v is not None:
        dims["bundle_coordination"] = min(v / 100, 1.0)

    # 4. Fresh wallet ratio
    v = _f(ha.get("new_wallets_pct"))
    if v is not None:
        dims["fresh_wallet_ratio"] = min(v / 100, 1.0)

    # 5. CEX dependency
    v = _f(ha.get("exchange_funded_pct"))
    if v is not None:
        dims["cex_dependency"] = min(v / 100, 1.0)

    # 6. Flow concentration
    v = _f(fa.get("top_mover_share_pct"))
    if v is not None:
        dims["flow_concentration"] = min(v / 100, 1.0)

    # 7. Liquidity fragility (from lp_locked or liquidity fields)
    lp = _f(scan.get("lp_locked_pct")) or _f(scan.get("lp_locked"))
    if lp is not None:
        if lp <= 1.0:
            lp *= 100  # normalize fraction to pct
        dims["liquidity_fragility"] = min((100 - lp) / 100, 1.0)

    # 8. Authority risk (binary flags → composite)
    auth = 0.0
    if scan.get("honeypot") is True:
        auth += 0.5
    mint = scan.get("mint_auth") or scan.get("mint_authority")
    if mint is True or str(mint).lower() in ("true", "mint", "active"):
        auth += 0.3
    freeze = scan.get("freeze_auth") or scan.get("freeze_authority")
    if freeze is True or str(freeze).lower() in ("true", "freeze", "active"):
        auth += 0.2
    if auth > 0:
        dims["authority_risk"] = min(auth, 1.0)

    return dims if len(dims) >= 3 else None


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """Cosine similarity over shared dimensions."""
    shared = set(a.keys()) & set(b.keys())
    if not shared:
        return 0.0
    dot = sum(a[d] * b[d] for d in shared)
    mag_a = math.sqrt(sum(a[d] ** 2 for d in shared))
    mag_b = math.sqrt(sum(b[d] ** 2 for d in shared))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def classify(dna: dict[str, float]) -> dict[str, Any]:
    """Match DNA against rug centroids. Returns best match + all similarities."""
    matches = []
    for pattern_name, centroid in RUG_CENTROIDS.items():
        sim = _cosine_similarity(dna, centroid)
        matches.append({"pattern": pattern_name, "similarity": round(sim, 4)})
    matches.sort(key=lambda m: m["similarity"], reverse=True)

    best = matches[0]
    similarity = best["similarity"]

    if similarity >= 0.85:
        threat = "CRITICAL_MATCH"
    elif similarity >= 0.70:
        threat = "HIGH_SIMILARITY"
    elif similarity >= 0.50:
        threat = "MODERATE_RESEMBLANCE"
    else:
        threat = "LOW_RESEMBLANCE"

    return {
        "best_match": best,
        "all_matches": matches,
        "threat_level": threat,
        "dna_dimensions": len(dna),
        "read": (
            f"Behavioral DNA {similarity:.0%} similar to {best['pattern']} "
            f"pattern ({threat.replace('_', ' ').lower()})"
        ),
    }


async def analyze_token_dna(scan_result: dict[str, Any]) -> dict[str, Any]:
    """Full DNA analysis pipeline: extract → classify → report."""
    dna = extract_dna(scan_result)
    if dna is None:
        return {"error": "insufficient scan data for DNA extraction (need ≥3 dimensions)"}

    classification = classify(dna)
    return {
        "dna": dna,
        "dimensions_available": len(dna),
        "classification": classification,
        "headline": classification["read"],
        "methodology": (
            "Behavioral fingerprint across 8 dimensions compared via "
            "cosine similarity against known rug-pattern centroids. "
            "Not a guarantee — a screening tool that improves with data."
        ),
    }
