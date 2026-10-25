#!/usr/bin/env python3
"""Stars-paid token monitors — read path (active window, decoded baseline).

A monitor is live only while its ``expires_at`` window is open. A NULL, zero
or unparseable window FAILS CLOSED (treated as expired), so a row written
without a window can never buy permanent coverage.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ._base import get_db

logger = logging.getLogger("rugmunchbot.db.monitor_reads")


def _parse_baseline(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        logger.debug("monitor_baseline_unparseable raw=%r", str(raw)[:80])
        return {}
    return value if isinstance(value, dict) else {}


def _decode(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["baseline"] = _parse_baseline(item.get("baseline_json"))
    return item


def _expired(item: dict[str, Any]) -> bool:
    """True unless the window is a positive future timestamp (fail CLOSED)."""
    try:
        expires = int(item.get("expires_at"))
    except (TypeError, ValueError):
        return True
    return expires <= 0 or expires < int(time.time())


def get_monitors(user_id: int) -> list[dict[str, Any]]:
    """Active rows for one user (may include a row whose window just closed)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM monitors WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_decode(r) for r in rows]


def get_active_monitors() -> list[dict[str, Any]]:
    """All active, non-expired monitors (for the delta background job)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM monitors WHERE is_active = 1 ORDER BY created_at ASC"
        ).fetchall()
    finally:
        conn.close()
    return [m for m in (_decode(r) for r in rows) if not _expired(m)]


__all__ = ["get_active_monitors", "get_monitors"]
