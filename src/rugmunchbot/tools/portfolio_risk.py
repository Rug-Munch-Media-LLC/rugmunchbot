"""Portfolio Risk Auditor — batch-scan multiple tokens for aggregate risk.

A degen sends 3-10 token addresses; we scan each through volume_integrity
and produce a portfolio-level risk report:
    - overall risk grade (A-F)
    - per-token breakdown (verdict + score)
    - concentration warnings (too many tokens in same risk band)
    - actionable summary ("2 of your 5 tokens are LIKELY_WASHED")

This is the feature that turns individual scans into portfolio intelligence.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from rugmunchbot.tools.volume_integrity import volume_integrity

logger = logging.getLogger(__name__)

_GRADES = {
    "A": {"max_prob_risky": 0.0, "label": "CLEAN PORTFOLIO"},
    "B": {"max_prob_risky": 0.2, "label": "LOW RISK"},
    "C": {"max_prob_risky": 0.4, "label": "MODERATE RISK"},
    "D": {"max_prob_risky": 0.6, "label": "HIGH RISK"},
    "F": {"max_prob_risky": 1.1, "label": "CRITICAL — IMMEDIATE REVIEW"},
}


async def _scan_one(address: str) -> dict[str, Any] | None:
    """Scan a single token via volume_integrity."""
    try:
        result = await volume_integrity(address=address)
        return result.data if result.ok else None
    except Exception as exc:
        logger.debug("portfolio:scan_failed %s err=%s", address[:12], str(exc)[:80])
        return None


async def audit_portfolio(addresses: list[str]) -> dict[str, Any]:
    """Batch-scan tokens and produce aggregate risk report."""
    # Deduplicate addresses
    unique = list(dict.fromkeys(a.strip().lower() for a in addresses if a.strip()))
    if not unique:
        return {"error": "no valid addresses provided"}

    # Scan all in parallel
    tasks = [_scan_one(addr) for addr in unique]
    results = await asyncio.gather(*tasks)

    scored: list[dict[str, Any]] = []
    failed: list[str] = []
    for addr, r in zip(unique, results, strict=False):
        if r is None:
            failed.append(addr)
            continue
        verdict = r.get("verdict", "UNKNOWN")
        score = r.get("score", 50)
        risky = verdict in ("LIKELY_WASHED", "SUSPECT")
        scored.append({
            "address": addr,
            "verdict": verdict,
            "score": score,
            "risky": risky,
            "pair": r.get("pair", ""),
            "liquidity_usd": r.get("liquidity_usd"),
        })

    if not scored:
        return {"error": "all scans failed — no data available"}

    # Aggregate
    total = len(scored)
    risky_count = sum(1 for s in scored if s["risky"])
    risky_pct = risky_count / total
    avg_score = sum(s["score"] for s in scored) / total

    # Grade
    grade = "F"
    for letter, cfg in sorted(_GRADES.items(), key=lambda x: x[1]["max_prob_risky"]):
        if risky_pct <= cfg["max_prob_risky"]:
            grade = letter
            break

    # Concentration warning
    bands: dict[str, int] = {}
    for s in scored:
        bands[s["verdict"]] = bands.get(s["verdict"], 0) + 1
    concentration_warning = None
    if total >= 3 and risky_pct >= 0.5:
        concentration_warning = (
            f"{risky_count}/{total} tokens ({risky_pct:.0%}) flagged as risky — "
            f"consider diversifying out of high-risk positions"
        )

    # Worst performers
    worst = sorted(scored, key=lambda x: x["score"])[:3]

    return {
        "grade": grade,
        "grade_label": _GRADES[grade]["label"],
        "total_tokens": total,
        "risky_count": risky_count,
        "risky_pct": round(risky_pct * 100, 1),
        "avg_score": round(avg_score, 1),
        "tokens": scored,
        "failed_addresses": failed,
        "worst_performers": [
            {"address": w["address"][:16] + "…", "verdict": w["verdict"], "score": w["score"]}
            for w in worst
        ],
        "concentration_warning": concentration_warning,
        "verdict_distribution": bands,
    }
