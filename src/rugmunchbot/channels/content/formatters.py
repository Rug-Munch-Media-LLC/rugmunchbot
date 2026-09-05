"""One-line formatting helpers for channel content (pure string building)."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

#: Scanner gate the backend applies before an item appears on
#: ``/api/v1/launches/alerts`` (documented: tokens auto-scanned above 70/100
#: risk). Used only to render the honest "condition met vs current" line.
ALERT_RISK_THRESHOLD: int = 70

__all__ = [
    "ALERT_RISK_THRESHOLD",
    "_alert_condition",
    "_scan_score_line",
    "_webhook_event_label",
]


def _alert_condition(item: dict[str, Any]) -> str:
    """One-line "condition met vs current" for a scanner alert item.

    When the payload carries a numeric ``risk_score`` the line states the
    current score against the documented alert threshold; otherwise it falls
    back to the risk-level verdict. Pure string building — never raises.
    """
    risk_level = str(item.get("risk_level") or "unknown")
    score = item.get("risk_score")
    if score is not None:
        with suppress(TypeError, ValueError):
            return f"risk {float(score):.0f}/100 ≥ {ALERT_RISK_THRESHOLD} ({risk_level})"
    return f"flagged {risk_level}"


def _scan_score_line(item: dict[str, Any]) -> str:
    """One-line score line for a general scan note (no alert-threshold claim).

    The launch-alert rail states "risk N/100 ≥ 70"; a general scan-history
    record only states its own score/level — we never claim a threshold that
    was not applied to it. Pure string building — never raises.
    """
    risk_level = str(item.get("risk_level") or "unknown")
    score = item.get("risk_score")
    if score is not None:
        with suppress(TypeError, ValueError):
            return f"risk {float(score):.0f}/100 ({risk_level})"
    return f"flagged {risk_level}"


def _webhook_event_label(event_type: str) -> str:
    """Readable event-type label for a webhook alert ("deployer_dump" → "deployer dump")."""
    return (event_type or "webhook").replace("_", " ").strip() or "webhook"
