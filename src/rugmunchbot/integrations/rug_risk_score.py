"""Rug-risk one-line verdict — OSS fallback.

The live bot renders the consolidated rug-risk scorer's verdict text
(``app.domains.scanner.rug_risk_score.risk_verdict_text``). OSS ships a
neutral fallback that never fabricates risk data.
"""

from __future__ import annotations

from typing import Any


def risk_verdict_text(score: dict[str, Any] | float | None) -> str:
    """Return a neutral verdict line (never asserts risk in OSS)."""
    if isinstance(score, dict):
        risk = score.get("risk_score") or score.get("score")
        level = score.get("risk_level")
        if level:
            return f"Risk level: {level}"
        if risk is not None:
            try:
                return f"Risk score: {float(risk):.0f}/100"
            except (TypeError, ValueError):
                return "Risk score: N/A"
    return "Risk: not assessed (OSS build)"


__all__ = ["risk_verdict_text"]
