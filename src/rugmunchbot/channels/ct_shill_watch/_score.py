"""ct_shill_watch — wall-of-shame severity scoring (single pure function)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def score_kol(entry: dict[str, Any]) -> float:
    """Shill-severity score: tokens shilled x coordination x recency.

    Pure. Higher = worse. Used by the daily refresh to keep ``severity``
    honest as evidence accumulates.
    """
    tokens = len(entry.get("tokens_shilled") or [])
    count = int(entry.get("shill_count") or 0)
    coordinated = 2.0 if entry.get("coordinated") else 1.0
    recency = 1.0
    ts = str(entry.get("last_shill_ts") or "")
    if ts:
        try:
            age_h = (datetime.now(UTC) - datetime.fromisoformat(ts)).total_seconds() / 3600.0
            recency = max(0.25, 1.0 - min(age_h, 720) / 720.0)
        except ValueError:
            recency = 0.5
    return round((tokens * 1.5 + count) * coordinated * recency, 2)
