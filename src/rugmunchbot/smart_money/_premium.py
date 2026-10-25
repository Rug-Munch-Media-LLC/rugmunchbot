"""Premium decision for one smart-money payload."""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.premium_signal import premium_verdict


def premium_status(item: dict[str, Any] | None) -> dict[str, Any]:
    """Verdict envelope for the premium lane ('' item is never premium)."""
    verdict = premium_verdict(item or {})
    return {
        "eligible": verdict.eligible,
        "score": verdict.score,
        "missing": list(verdict.missing),
        "reasons": list(verdict.reasons),
    }


__all__ = ["premium_status"]
