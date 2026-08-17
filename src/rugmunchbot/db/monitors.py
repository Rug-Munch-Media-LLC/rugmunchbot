#!/usr/bin/env python3
"""Stars-paid token monitor CRUD for the rugmunchbot DB layer.

Each row is one paid monitor: a token security snapshot (``baseline_json``)
plus the subscription window (``expires_at``). The backend webhook engine
delivers live LP/whale/price events through the existing alert bridge; this
table stores the local bookkeeping (who monitors what, until when) and the
baseline used by the periodic tax/ownership/mint delta job
(``handlers/alerts.check_monitors``).
"""

from __future__ import annotations

import json
import time
from typing import Any

from ._base import get_db

MONITOR_DEFAULT_DAYS = 30


def add_monitor(
    user_id: int,
    token_address: str,
    chain: str,
    symbol: str | None = None,
    deployer_address: str | None = None,
    baseline: dict[str, Any] | None = None,
    days: int = MONITOR_DEFAULT_DAYS,
) -> bool:
    """Register a paid monitor. Returns False when the row already exists."""
    conn = get_db()
    existing = conn.execute(
        "SELECT id FROM monitors WHERE user_id = ? AND token_address = ? AND is_active = 1",
        (user_id, token_address.lower()),
    ).fetchone()
    if existing:
        conn.close()
        return False
    now = int(time.time())
    conn.execute(
        "INSERT INTO monitors (user_id, token_address, chain, symbol, deployer_address, baseline_json, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            token_address.lower(),
            chain,
            symbol,
            deployer_address,
            json.dumps(baseline or {}),
            now,
            now + days * 86400,
        ),
    )
    conn.commit()
    conn.close()
    return True


def get_monitors(user_id: int) -> list[dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM monitors WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [_decode(m) for m in rows]


def get_active_monitors() -> list[dict[str, Any]]:
    """All active, non-expired monitors (for the delta background job)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM monitors WHERE is_active = 1 ORDER BY created_at ASC",
    ).fetchall()
    conn.close()
    decoded = [_decode(m) for m in rows]
    return [m for m in decoded if not _expired(m)]


def remove_monitor(user_id: int, token_address: str) -> bool:
    conn = get_db()
    cursor = conn.execute(
        "UPDATE monitors SET is_active = 0 WHERE user_id = ? AND token_address = ?",
        (user_id, token_address.lower()),
    )
    conn.commit()
    result = cursor.rowcount > 0
    conn.close()
    return result


def update_monitor_baseline(monitor_id: int, baseline: dict[str, Any]) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE monitors SET baseline_json = ? WHERE id = ?",
        (json.dumps(baseline or {}), monitor_id),
    )
    conn.commit()
    conn.close()


def expire_monitors() -> int:
    """Deactivate monitors past their paid window. Returns rows expired."""
    conn = get_db()
    now = int(time.time())
    cursor = conn.execute(
        "UPDATE monitors SET is_active = 0 WHERE is_active = 1 AND expires_at IS NOT NULL AND expires_at < ?",
        (now,),
    )
    conn.commit()
    result = cursor.rowcount
    conn.close()
    return result


def _decode(row: Any) -> dict[str, Any]:
    item = dict(row)
    raw = item.get("baseline_json")
    item["baseline"] = {}
    if raw:
        try:
            item["baseline"] = json.loads(raw)
        except (ValueError, TypeError):
            item["baseline"] = {}
    return item


def _expired(item: dict[str, Any]) -> bool:
    exp = item.get("expires_at")
    return exp is not None and int(exp) < int(time.time())


__all__ = [
    "MONITOR_DEFAULT_DAYS",
    "add_monitor",
    "expire_monitors",
    "get_active_monitors",
    "get_monitors",
    "remove_monitor",
    "update_monitor_baseline",
]
