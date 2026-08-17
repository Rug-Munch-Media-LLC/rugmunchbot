#!/usr/bin/env python3
"""Watchlist CRUD for the rugmunchbot DB layer."""

from __future__ import annotations

import time
from typing import Any

from ._base import get_db
from .tiers import get_user_tier


def add_watchlist(
    user_id: int,
    token_address: str,
    chain: str,
    symbol: str | None = None,
    alert_price: float | None = None,
    alert_dir: str | None = None,
) -> bool:
    tier = get_user_tier(user_id)
    max_items = {"free": 0, "scout": 10, "hunter": 50, "pro": -1}.get(tier, 0)
    if max_items == 0:
        return False
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM watchlists WHERE user_id = ? AND is_active = 1", (user_id,)
    ).fetchone()["c"]
    if max_items != -1 and count >= max_items:
        conn.close()
        return False
    conn.execute(
        "INSERT INTO watchlists (user_id, token_address, chain, symbol, alert_price, alert_direction, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, token_address, chain, symbol, alert_price, alert_dir, int(time.time())),
    )
    conn.commit()
    conn.close()
    return True


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
