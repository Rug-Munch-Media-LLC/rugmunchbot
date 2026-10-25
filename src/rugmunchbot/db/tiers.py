#!/usr/bin/env python3
"""Tier management for the rugmunchbot DB layer.

A paid tier is only live while its ``tier_expires`` window is open. A NULL,
zero or unparseable window FAILS CLOSED (the user is downgraded to ``free``),
so a missing window can never grant permanent paid access. Writers refuse to
create an unbounded window in the first place (``days`` must be positive).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from ._base import get_db

logger = logging.getLogger("rugmunchbot.db.tiers")

MAX_TIER_DAYS = 3650


def _window_open(expires: Any) -> bool:
    """True only when a paid window is a positive timestamp in the future.

    NULL, ``0``, negative and non-numeric values all fail CLOSED.
    """
    try:
        return int(expires) > int(time.time())
    except (TypeError, ValueError):
        logger.debug("tier_window_unparseable expires=%r", expires)
        return False


def _downgrade_to_free(user_id: int) -> None:
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET tier = 'free', tier_expires = 0 WHERE user_id = ?", (user_id,)
        )
        conn.commit()
    finally:
        conn.close()


def _window_end(days: int) -> int:
    return int(datetime.now(UTC).timestamp() + timedelta(days=days).total_seconds())


def _validated_days(days: int) -> int:
    """Refuse to mint an unbounded (or absurdly long) paid window."""
    value = int(days)
    if not 1 <= value <= MAX_TIER_DAYS:
        msg = f"paid tier window must be 1..{MAX_TIER_DAYS} days, got {value}"
        raise ValueError(msg)
    return value


def get_user_tier(user_id: int) -> str:
    from rugmunchbot.config import BETA_MODE

    if BETA_MODE:
        return "pro"  # Beta: all features unlocked for everyone
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT tier, tier_expires FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not row:
            return "free"
        tier = str(row["tier"] or "free").strip().lower() or "free"
        # A pre-v1 row can carry NULL here (the column arrived via ALTER TABLE) and
        # ``None > 0`` raises TypeError, so an expired subscription would crash
        # every command instead of downgrading. Coerce before comparing.
        expires = int(row["tier_expires"] or 0)
        # Fail closed: a paid tier is only valid inside a recorded window. NULL/0
        # on a paid row means the activation never committed, so the user is free.
        if tier != "free" and (expires <= 0 or time.time() > expires):
            conn.execute(
                "UPDATE users SET tier = 'free', tier_expires = 0 WHERE user_id = ?", (user_id,)
            )
            conn.commit()
            return "free"
        return tier
    finally:
        conn.close()


def set_user_tier(user_id: int, tier: str, days: int = 30) -> None:
    """Set a user's tier. ``free`` clears the window; paid tiers need a window."""
    expires = 0 if tier == "free" else _window_end(_validated_days(days))
    conn = get_db()
    conn.execute(
        "UPDATE users SET tier = ?, tier_expires = ? WHERE user_id = ?", (tier, expires, user_id)
    )
    conn.commit()
    conn.close()


def activate_tier(user_id: int, tier: str, days: int = 30) -> None:
    """Atomically activate a paid tier for N days (sets tier + tier_expires)."""
    expires = _window_end(_validated_days(days))
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


#: Usage counter -> the tier field holding its weekly allowance.
_TIER_KEYS: dict[str, str] = {"scans": "scans_per_week", "ai_msgs": "ai_msgs_per_week"}

#: The atomic claim for each counter. The ``WHERE`` clause is what makes the
#: limit real under concurrency: the database, not a prior SELECT, decides
#: whether there is room, so two simultaneous requests cannot both take the
#: last unit. ``rowcount`` is 1 when the unit was claimed and 0 when not.
_CLAIM: dict[str, str] = {
    "scans": (
        "INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs) VALUES (?, ?, 1, 0) "
        "ON CONFLICT(user_id, week_start) DO UPDATE SET scans = scans + 1 WHERE scans < ?"
    ),
    "ai_msgs": (
        "INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs) VALUES (?, ?, 0, 1) "
        "ON CONFLICT(user_id, week_start) DO UPDATE SET ai_msgs = ai_msgs + 1 WHERE ai_msgs < ?"
    ),
}

#: Guarded bonus debit — ``bonus > 0`` in the WHERE makes the balance unspendable twice.
_DEBIT: dict[str, str] = {
    "scans": "UPDATE users SET bonus_scans = bonus_scans - 1 WHERE user_id = ? AND bonus_scans > 0",
    "ai_msgs": "UPDATE users SET bonus_ai_msgs = bonus_ai_msgs - 1 WHERE user_id = ? AND bonus_ai_msgs > 0",
}

#: Lifetime totals, kept per counter so a refund is a single inverse row.
_TOTAL: dict[str, str] = {
    "scans": "UPDATE users SET total_scans = total_scans + 1 WHERE user_id = ?",
    "ai_msgs": "UPDATE users SET total_ai_msgs = total_ai_msgs + 1 WHERE user_id = ?",
}


def increment_usage(user_id: int, scan: bool = False, ai_msg: bool = False) -> bool:
    """Consume one scan / AI question, atomically. False when no quota was taken.

    Every counter is claimed by a single conditional statement, so a second
    concurrent request cannot spend the same unit: the allowance is decremented
    by the database under the ``WHERE`` guard, never by a read-then-write here.
    Allocation order is weekly allowance, then purchased bonus balance.
    """
    from rugmunchbot.config import TIERS

    wanted = [key for key, flag in (("scans", scan), ("ai_msgs", ai_msg)) if flag]
    if not wanted:
        return True
    week = _current_week_start()
    tier = get_user_tier(user_id)  # its own connection — call before the write txn
    definition = TIERS.get(tier, TIERS["free"])
    conn = get_db()
    try:
        for key in wanted:
            limit = int(definition.get(_TIER_KEYS[key], 0) or 0)
            claimed = conn.execute(_CLAIM[key], (user_id, week, limit)).rowcount if limit > 0 else 0
            if not claimed:
                claimed = conn.execute(_DEBIT[key], (user_id,)).rowcount
            if not claimed:
                conn.rollback()  # a partial consume of the *other* counter is undone too
                return False
            conn.execute(_TOTAL[key], (user_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def check_rate_limit(user_id: int, action: str = "scan") -> tuple[bool, int, int]:
    """Returns (allowed, current_used, max_allowed).
    'allowed' is True if weekly allotment OR bonus balance has room."""
    from rugmunchbot.config import TIERS

    tier = get_user_tier(user_id)
    tier_config = TIERS.get(tier, TIERS["free"])
    scans_used, ai_used = get_weekly_usage(user_id)

    conn = get_db()
    user = conn.execute(
        "SELECT bonus_scans, bonus_ai_msgs FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
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
