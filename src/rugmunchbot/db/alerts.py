#!/usr/bin/env python3
"""Price-alert queries and firing for the rugmunchbot DB layer.

Reads active watchlist entries that carry alert metadata (``alert_price`` +
``alert_direction``) and marks them triggered once fired.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ._base import get_db


def get_active_alerts() -> list[dict[str, Any]]:
    """Return all watchlist entries with active price alerts."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM watchlists
        WHERE alert_price IS NOT NULL
          AND alert_direction IS NOT NULL
          AND is_active = 1
        ORDER BY created_at DESC
        """,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alert_triggered(watch_id: int) -> None:
    """Mark an alert as fired by setting triggered_at and deactivating."""
    conn = get_db()
    conn.execute(
        "UPDATE watchlists SET is_active = 0, alert_triggered_at = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), watch_id),
    )
    conn.commit()
    conn.close()
