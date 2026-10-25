#!/usr/bin/env python3
"""Telegram Stars balance, spending and top-up logic for the rugmunchbot DB layer.

Every balance change is guarded in SQL (``stars_balance >= amount``) inside a
``BEGIN IMMEDIATE`` transaction, so two concurrent spends can never both see
the same balance and drive it negative.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from ._base import get_db

logger = logging.getLogger("rugmunchbot.db.stars")


def _deduct_stars(conn: sqlite3.Connection, user_id: int, amount: int) -> bool:
    """Deduct atomically; False when the balance is (or became) insufficient."""
    cursor = conn.execute(
        "UPDATE users SET stars_balance = stars_balance - ? WHERE user_id = ? AND stars_balance >= ?",
        (amount, user_id, amount),
    )
    return cursor.rowcount > 0


def _credit_bonus(conn: sqlite3.Connection, user_id: int, pack_type: str, amount: int) -> None:
    if pack_type == "scans":
        conn.execute(
            "UPDATE users SET bonus_scans = bonus_scans + ? WHERE user_id = ?", (amount, user_id)
        )
    elif pack_type == "ai_msgs":
        conn.execute(
            "UPDATE users SET bonus_ai_msgs = bonus_ai_msgs + ? WHERE user_id = ?",
            (amount, user_id),
        )


def add_stars(user_id: int, amount: int, action: str, payment_id: str | None = None) -> None:
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE users SET stars_balance = stars_balance + ? WHERE user_id = ?",
            (amount, user_id),
        )
        conn.execute(
            "INSERT INTO star_transactions (user_id, amount, action, telegram_payment_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, action, payment_id, int(time.time())),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        logger.warning("stars_credit_failed user_id=%s", user_id, exc_info=True)
        raise
    finally:
        conn.close()


def spend_stars(user_id: int, amount: int, action: str) -> bool:
    """Spend ``amount`` stars. False (nothing written) when the balance is short."""
    if amount < 0:
        return False
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not _deduct_stars(conn, user_id, amount):
            conn.rollback()
            return False
        conn.execute(
            "INSERT INTO star_transactions (user_id, amount, action, created_at) VALUES (?, ?, ?, ?)",
            (user_id, -amount, action, int(time.time())),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        logger.warning("stars_spend_failed user_id=%s", user_id, exc_info=True)
        return False
    finally:
        conn.close()


def apply_top_up(
    user_id: int,
    pack_type: str,
    amount: int,
    stars_cost: int,
    payment_id: str | None = None,
) -> bool:
    """Apply a top-up purchase: deduct stars, credit bonus balance."""
    if pack_type not in ("scans", "ai_msgs") or amount <= 0 or stars_cost < 0:
        return False
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not _deduct_stars(conn, user_id, stars_cost):
            conn.rollback()
            return False
        _credit_bonus(conn, user_id, pack_type, amount)
        conn.execute(
            "INSERT INTO star_transactions (user_id, amount, action, telegram_payment_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, -stars_cost, f"top_up_{pack_type}_{amount}", payment_id, int(time.time())),
        )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        logger.warning("stars_top_up_failed user_id=%s", user_id, exc_info=True)
        return False
    finally:
        conn.close()
