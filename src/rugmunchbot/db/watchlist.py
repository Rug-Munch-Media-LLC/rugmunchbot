#!/usr/bin/env python3
"""Watchlist CRUD for the rugmunchbot DB layer.

The per-tier item cap is enforced inside a ``BEGIN IMMEDIATE`` transaction, so
concurrent adds cannot race the slot count and overshoot the cap.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from ._base import get_db
from .tiers import get_user_tier

logger = logging.getLogger("rugmunchbot.db.watchlist")

#: Max active watchlist items per tier; -1 means unlimited, 0 means none.
WATCHLIST_CAPS = {"free": 0, "scout": 10, "hunter": 50, "pro": -1}


def _active_watch_count(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM watchlists WHERE user_id = ? AND is_active = 1",
        (user_id,),
    ).fetchone()
    return int(row["c"])


def add_watchlist(
    user_id: int,
    token_address: str,
    chain: str,
    symbol: str | None = None,
    alert_price: float | None = None,
    alert_dir: str | None = None,
) -> bool:
    """Add a watchlist item. False when the tier has no room (or no access)."""
    max_items = WATCHLIST_CAPS.get(get_user_tier(user_id), 0)
    if max_items == 0:
        return False
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if max_items > 0 and _active_watch_count(conn, user_id) >= max_items:
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO watchlists "
            "(user_id, token_address, chain, symbol, alert_price, alert_direction, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, token_address, chain, symbol, alert_price, alert_dir, int(time.time())),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        logger.warning("watchlist_add_failed user_id=%s", user_id, exc_info=True)
        return False
    finally:
        conn.close()


def get_watchlist(user_id: int) -> list[dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM watchlists WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def remove_watchlist(user_id: int, token_address: str) -> bool:
    conn = get_db()
    cursor = conn.execute(
        "UPDATE watchlists SET is_active = 0 WHERE user_id = ? AND token_address = ?",
        (user_id, token_address),
    )
    conn.commit()
    result = cursor.rowcount > 0
    conn.close()
    return result


def get_watchlist_for_user(user_id: int, include_triggered: bool = False) -> list[dict[str, Any]]:
    """Get user watchlist with alert metadata.

    If include_triggered is True, returns both active and triggered entries.
    """
    conn = get_db()
    if include_triggered:
        rows = conn.execute(
            "SELECT * FROM watchlists WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM watchlists WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
