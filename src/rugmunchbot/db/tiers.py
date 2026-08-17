#!/usr/bin/env python3
"""Tier management, weekly usage quotas and rate limiting for the rugmunchbot DB layer."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from ._base import get_db


def get_user_tier(user_id: int) -> str:
    from app.domains.telegram.rugmunchbot.config import BETA_MODE

    if BETA_MODE:
        return "pro"  # Beta: all features unlocked for everyone
    conn = get_db()
    row = conn.execute("SELECT tier, tier_expires FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return "free"
    if row["tier"] != "free" and row["tier_expires"] > 0 and time.time() > row["tier_expires"]:
        conn = get_db()
        conn.execute("UPDATE users SET tier = 'free', tier_expires = 0 WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        return "free"
    return row["tier"]


def set_user_tier(user_id: int, tier: str, days: int = 30) -> None:
    expires = int(time.time() + timedelta(days=days).total_seconds()) if tier != "free" else 0
    conn = get_db()
    conn.execute("UPDATE users SET tier = ?, tier_expires = ? WHERE user_id = ?", (tier, expires, user_id))
    conn.commit()
    conn.close()


def activate_tier(user_id: int, tier: str, days: int = 30) -> None:
    """Atomically activate a paid tier for N days (sets tier + tier_expires)."""
    expires = int(time.time() + timedelta(days=days).total_seconds())
    conn = get_db()
    conn.execute(
        "UPDATE users SET tier = ?, tier_expires = ? WHERE user_id = ?",
        (tier, expires, user_id),
    )
    conn.commit()
    conn.close()


# ── Weekly Usage ──


def _current_week_start() -> str:
    """Return Monday of the current UTC week as YYYY-MM-DD."""
    now = datetime.now(UTC)
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


def get_weekly_usage(user_id: int) -> tuple[int, int]:
    week = _current_week_start()
    conn = get_db()
    row = conn.execute(
        "SELECT scans, ai_msgs FROM weekly_usage WHERE user_id = ? AND week_start = ?",
        (user_id, week),
    ).fetchone()
    conn.close()
    return (row["scans"], row["ai_msgs"]) if row else (0, 0)


def increment_usage(user_id: int, scan: bool = False, ai_msg: bool = False) -> bool:
    """Consume 1 scan or AI msg. Tries weekly allotment first, then bonus balance."""
    week = _current_week_start()
    conn = get_db()

    if scan:
        # Check if weekly allotment has room
        row = conn.execute(
            "SELECT scans FROM weekly_usage WHERE user_id = ? AND week_start = ?", (user_id, week)
        ).fetchone()
        user = conn.execute("SELECT bonus_scans FROM users WHERE user_id = ?", (user_id,)).fetchone()
        from app.domains.telegram.rugmunchbot.config import TIERS

        tier = get_user_tier(user_id)
        limit = TIERS.get(tier, TIERS["free"]).get("scans_per_week", 25)
        current = row["scans"] if row else 0

        if current < limit:
            # Use weekly allotment
            conn.execute(
                """
                INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs)
                VALUES (?, ?, 1, 0)
                ON CONFLICT(user_id, week_start) DO UPDATE SET scans = scans + 1
            """,
                (user_id, week),
            )
        elif user and user["bonus_scans"] > 0:
            # Use bonus balance
            conn.execute("UPDATE users SET bonus_scans = bonus_scans - 1 WHERE user_id = ?", (user_id,))
        else:
            conn.close()
            return False  # No quota available

        conn.execute("UPDATE users SET total_scans = total_scans + 1 WHERE user_id = ?", (user_id,))

    if ai_msg:
        row = conn.execute(
            "SELECT ai_msgs FROM weekly_usage WHERE user_id = ? AND week_start = ?", (user_id, week)
        ).fetchone()
        user = conn.execute("SELECT bonus_ai_msgs FROM users WHERE user_id = ?", (user_id,)).fetchone()
        from app.domains.telegram.rugmunchbot.config import TIERS

        tier = get_user_tier(user_id)
        limit = TIERS.get(tier, TIERS["free"]).get("ai_msgs_per_week", 15)
        current = row["ai_msgs"] if row else 0

        if current < limit:
            conn.execute(
                """
                INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs)
                VALUES (?, ?, 0, 1)
                ON CONFLICT(user_id, week_start) DO UPDATE SET ai_msgs = ai_msgs + 1
            """,
                (user_id, week),
            )
        elif user and user["bonus_ai_msgs"] > 0:
            conn.execute("UPDATE users SET bonus_ai_msgs = bonus_ai_msgs - 1 WHERE user_id = ?", (user_id,))
        else:
            conn.close()
            return False

        conn.execute("UPDATE users SET total_ai_msgs = total_ai_msgs + 1 WHERE user_id = ?", (user_id,))

    conn.commit()
    conn.close()
    return True


def check_rate_limit(user_id: int, action: str = "scan") -> tuple[bool, int, int]:
    """Returns (allowed, current_used, max_allowed).
    'allowed' is True if weekly allotment OR bonus balance has room."""
    from app.domains.telegram.rugmunchbot.config import TIERS

    tier = get_user_tier(user_id)
    tier_config = TIERS.get(tier, TIERS["free"])
    scans_used, ai_used = get_weekly_usage(user_id)

    conn = get_db()
    user = conn.execute("SELECT bonus_scans, bonus_ai_msgs FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    bonus_s = user["bonus_scans"] if user else 0
    bonus_a = user["bonus_ai_msgs"] if user else 0

    if action == "scan":
        limit = tier_config.get("scans_per_week", 25)
        current = scans_used
        bonus = bonus_s
    else:
        limit = tier_config.get("ai_msgs_per_week", 15)
        current = ai_used
        bonus = bonus_a

    # Allowed if weekly allotment has room OR bonus balance > 0
    allowed = (current < limit) or (bonus > 0)
    return allowed, current, limit
