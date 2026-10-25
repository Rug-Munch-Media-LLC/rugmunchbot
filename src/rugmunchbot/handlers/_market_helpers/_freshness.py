"""Feed freshness label for trending — stale after 10 minutes (pure)."""

from __future__ import annotations

import time

STALE_AFTER_S = 600


def freshness_label(fetched_at: float | None, now: float | None = None) -> str:
    """Return ``''`` when fresh/unknown, else a stale label with the age.

    ``fetched_at`` is an epoch second carried by the feed entry. Unknown
    timestamps are treated as fresh (we never invent a staleness claim).
    """
    if fetched_at is None:
        return ""
    try:
        age = (now if now is not None else time.time()) - float(fetched_at)
    except (TypeError, ValueError):
        return ""
    if age <= STALE_AFTER_S:
        return ""
    return f"stale \u2014 updated {int(age // 60)} min ago"
