#!/usr/bin/env python3
"""Stars-paid token monitors — write path (claim / update / expire).

Each row is one paid monitor: a token security snapshot (``baseline_json``)
plus the subscription window (``expires_at``). Slots are claimed inside a
single ``BEGIN IMMEDIATE`` transaction, so concurrent registrations can
neither exceed the slot cap nor double-insert the same token, and an
unbounded monitor (``days <= 0``) is never written at all.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from ._base import get_db

logger = logging.getLogger("rugmunchbot.db.monitors")

MONITOR_DEFAULT_DAYS = 30
SECONDS_PER_DAY = 86400


@dataclass(frozen=True, slots=True)
class MonitorSpec:
    """Registration payload for one monitor row."""

    chain: str = ""
    symbol: str | None = None
    deployer_address: str | None = None
    baseline: dict[str, Any] | None = None


def _window_end(now: int, days: int) -> int:
    return now + days * SECONDS_PER_DAY


def _has_active_slot(conn: sqlite3.Connection, user_id: int, address: str) -> bool:
    row = conn.execute(
        "SELECT id FROM monitors WHERE user_id = ? AND token_address = ? AND is_active = 1 LIMIT 1",
        (user_id, address),
    ).fetchone()
    return row is not None


def _active_slot_count(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM monitors WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    return int(row["c"])


def claim_monitor_slot(
    user_id: int,
    token_address: str,
    spec: MonitorSpec,
    days: int = MONITOR_DEFAULT_DAYS,
    max_active: int | None = None,
) -> bool:
    """Atomically claim one monitor slot, always window-bounded.

    Returns False when the address is empty, ``days`` is not positive (an
    unbounded monitor is never written), the user already has an active
    monitor on this token, or ``max_active`` slots are already used.
    """
    address = (token_address or "").strip().lower()
    if not address or days <= 0:
        return False
    now = int(time.time())
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _has_active_slot(conn, user_id, address):
            conn.rollback()
            return False
        if (
            max_active is not None
            and max_active >= 0
            and _active_slot_count(conn, user_id) >= max_active
        ):
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO monitors "
            "(user_id, token_address, chain, symbol, deployer_address, baseline_json, created_at, expires_at, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                user_id,
                address,
                spec.chain,
                spec.symbol,
                spec.deployer_address,
                json.dumps(spec.baseline or {}),
                now,
                _window_end(now, days),
            ),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        logger.warning("monitor_claim_failed user_id=%s", user_id, exc_info=True)
        return False
    finally:
        conn.close()


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
    spec = MonitorSpec(
        chain=chain, symbol=symbol, deployer_address=deployer_address, baseline=baseline
    )
    return claim_monitor_slot(user_id, token_address, spec, days=days)


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
    """Deactivate monitors past their paid window. Returns rows expired.

    Rows with a NULL / non-positive ``expires_at`` expire too: an unbounded
    window fails CLOSED rather than granting free permanent coverage.
    """
    conn = get_db()
    cursor = conn.execute(
        "UPDATE monitors SET is_active = 0 "
        "WHERE is_active = 1 AND (expires_at IS NULL OR expires_at <= 0 OR expires_at < ?)",
        (int(time.time()),),
    )
    conn.commit()
    result = cursor.rowcount
    conn.close()
    return result


__all__ = [
    "MONITOR_DEFAULT_DAYS",
    "MonitorSpec",
    "add_monitor",
    "claim_monitor_slot",
    "expire_monitors",
    "remove_monitor",
    "update_monitor_baseline",
]
