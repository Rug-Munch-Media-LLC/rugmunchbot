"""Midday Market Report — story freshness for the macro lane.

Undated stories are KEPT. Telegram-sourced reranker items carry no
``published_at``; dropping them emptied the macro lane and forced the
writer into a SKIP (2026-10-17 zero-post incident).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def is_fresh_or_undated(item: dict[str, Any], cutoff: datetime) -> bool:
    """True when a story is inside the window OR carries no usable timestamp."""
    raw = item.get("published_at")
    if not raw:
        return True
    try:
        if isinstance(raw, (int, float)):
            ts = float(raw)
            if ts > 1e12:
                ts /= 1000.0
            dt = datetime.fromtimestamp(ts, tz=UTC)
        else:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
    except (ValueError, TypeError, OSError):
        return True
    return dt >= cutoff


__all__ = ["is_fresh_or_undated"]
