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
    finally:
        conn.close()
    if not row:
        return "free"
    tier = str(row["tier"] or "free")
    if tier == "free":
        return "free"
    if not _window_open(row["tier_expires"]):
        logger.info("tier_window_closed user_id=%s tier=%s", user_id, tier)
        _downgrade_to_free(user_id)
        return "free"
    return tier


def set_user_tier(user_id: int, tier: str, days: int = 30) -> None:
    """Set a user's tier. ``free`` clears the window; paid tiers need a window."""
    expires = 0 if tier == "free" else _window_end(_validated_days(days))
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET tier = ?, tier_expires = ? WHERE user_id = ?",
            (tier, expires, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def activate_tier(user_id: int, tier: str, days: int = 30) -> None:
    """Atomically activate a paid tier for N days (sets tier + tier_expires)."""
    expires = _window_end(_validated_days(days))
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET tier = ?, tier_expires = ? WHERE user_id = ?",
            (tier, expires, user_id),
        )
        conn.commit()
    finally:
        conn.close()
