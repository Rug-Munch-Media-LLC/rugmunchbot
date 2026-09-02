"""Composite + determination for the RiskReport (mirrors risk_report).

Split out of ``formatters/_scan_report_riskreport.py`` during the de-godding
pass. ``_composite_and_determination`` computes the weighted-average composite
over KNOWN factors and the high-level rug/scam/risky/ok verdict. Pure module:
no network, no I/O.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._scan_report_riskreport._constants import (
    _FACTOR_WEIGHTS,
    _MIN_KNOWN_FOR_DETERMINATION,
    _RISKY_MIN,
    _RUG_MIN,
    _SCAM_MIN,
)

# ═══════════════════════════════════════════════════════════════════════════
# COMPOSITE + DETERMINATION (mirrors risk_report._composite_and_determination)
# ═══════════════════════════════════════════════════════════════════════════


def _composite_and_determination(
    factors: list[dict[str, Any]],
    honeypot: dict[str, Any],
    deployer: dict[str, Any],
    bundle: dict[str, Any],
) -> tuple[int | None, str, str]:
    """Weighted-average composite over KNOWN factors + high-level verdict."""
    known = [f for f in factors if f.get("risk_score") is not None]
    if not known:
        return None, "unknown", "Insufficient data to score this token."

    total_w = 0.0
    total = 0.0
    for f in known:
        w = _FACTOR_WEIGHTS.get(f["key"], 1.0)
        total += float(f["risk_score"]) * w
        total_w += w
    composite = round(total / total_w) if total_w > 0 else 50

    determination: str
    if bool(honeypot.get("is_honeypot")) or composite >= _SCAM_MIN:
        determination = "scam"
    elif composite >= _RUG_MIN or (
        len(known) >= _MIN_KNOWN_FOR_DETERMINATION
        and bool(deployer.get("previously_rugged"))
        and bool(bundle.get("is_bundled"))
    ):
        determination = "rug"
    elif composite >= _RISKY_MIN:
        determination = "risky"
    elif composite < _RISKY_MIN:
        determination = "ok"
    else:
        determination = "unknown"

    summary = _summary_text(determination, composite, len(known), len(factors))
    return composite, determination, summary


def _summary_text(determination: str, composite: int | None, known: int, total: int) -> str:
    base = f"Composite risk {composite}/100 from {known}/{total} known factors."
    if determination == "scam":
        return f"{base} DETERMINATION: SCAM \u2014 decisive scam vectors detected; do not trade."
    if determination == "rug":
        return f"{base} DETERMINATION: RUG \u2014 strong rug-pull signals; extreme caution."
    if determination == "risky":
        return f"{base} DETERMINATION: RISKY \u2014 elevated risk; only trade with full awareness."
    if determination == "ok":
        return f"{base} DETERMINATION: OK \u2014 no dominant scam/rug signal detected."
    return f"{base} Determination: UNKNOWN \u2014 insufficient data."
