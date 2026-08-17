#!/usr/bin/env python3
"""Telegram Stars balance, spending and top-up logic for the rugmunchbot DB layer."""

from __future__ import annotations

import time

from ._base import get_db


def add_stars(user_id: int, amount: int, action: str, payment_id: str | None = None) -> None:
    conn = get_db()
    conn.execute("UPDATE users SET stars_balance = stars_balance + ? WHERE user_id = ?", (amount, user_id))
    conn.execute(
        "INSERT INTO star_transactions (user_id, amount, action, telegram_payment_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, amount, action, payment_id, int(time.time())),
    )
    conn.commit()
    conn.close()


def spend_stars(user_id: int, amount: int, action: str) -> bool:
    conn = get_db()
    row = conn.execute("SELECT stars_balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row or row["stars_balance"] < amount:
        conn.close()
        return False
    conn.execute("UPDATE users SET stars_balance = stars_balance - ? WHERE user_id = ?", (amount, user_id))
    conn.execute(
        "INSERT INTO star_transactions (user_id, amount, action, created_at) VALUES (?, ?, ?, ?)",
        (user_id, -amount, action, int(time.time())),
    )
    conn.commit()
    conn.close()
    return True


def apply_top_up(user_id: int, pack_type: str, amount: int, stars_cost: int, payment_id: str | None = None) -> bool:
    """Apply a top-up purchase: deduct stars, credit bonus balance."""
    conn = get_db()
    row = conn.execute("SELECT stars_balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if not row or row["stars_balance"] < stars_cost:
        conn.close()
        return False
    # Deduct stars
    conn.execute(
        "UPDATE users SET stars_balance = stars_balance - ? WHERE user_id = ?",
        (stars_cost, user_id),
    )
    # Credit bonus
    if pack_type == "scans":
        conn.execute("UPDATE users SET bonus_scans = bonus_scans + ? WHERE user_id = ?", (amount, user_id))
    elif pack_type == "ai_msgs":
        conn.execute(
            "UPDATE users SET bonus_ai_msgs = bonus_ai_msgs + ? WHERE user_id = ?",
            (amount, user_id),
        )
    # Log transaction
    conn.execute(
        "INSERT INTO star_transactions (user_id, amount, action, telegram_payment_id, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, -stars_cost, f"top_up_{pack_type}_{amount}", payment_id, int(time.time())),
    )
    conn.commit()
    conn.close()
    return True
