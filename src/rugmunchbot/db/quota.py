#!/usr/bin/env python3
"""Weekly usage quotas and rate-limit snapshots for the rugmunchbot DB layer.

Quota consumption is atomic: each claim opens a ``BEGIN IMMEDIATE``
transaction, taking SQLite's write lock *before* the counter is read, so
concurrent calls can never both see "one unit left" and both consume it.
Bonus balances use a guarded ``UPDATE ... WHERE bonus > 0``.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

from ._base import get_db
from .tiers import get_user_tier

logger = logging.getLogger("rugmunchbot.db.quota")

DEFAULT_SCANS_PER_WEEK = 25
DEFAULT_AI_MSGS_PER_WEEK = 15
QUOTA_SCAN = "scan"


def _current_week_start() -> str:
    """Return Monday of the current UTC week as YYYY-MM-DD."""
    now = datetime.now(UTC)
    monday = now - timedelta(days=now.weekday())
    return monday.strftime("%Y-%m-%d")


def _limit_for(tier: str, key: str, fallback: int) -> int:
    from rugmunchbot.config import TIERS

    return int(TIERS.get(tier, TIERS["free"]).get(key, fallback))


def get_weekly_usage(user_id: int) -> tuple[int, int]:
    """Return ``(scans, ai_msgs)`` consumed in the current UTC week."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT scans, ai_msgs FROM weekly_usage WHERE user_id = ? AND week_start = ?",
            (user_id, _current_week_start()),
        ).fetchone()
    finally:
        conn.close()
    return (row["scans"], row["ai_msgs"]) if row else (0, 0)


def _claim_week_slot(
    conn: sqlite3.Connection, user_id: int, week: str, *, scan: bool, limit: int
) -> bool:
    """Claim one weekly unit inside an open IMMEDIATE transaction.

    The caller already holds the write lock, so the read and the increment
    below cannot interleave with another writer.
    """
    if limit <= 0:
        return False
    if scan:
        return _claim_scan_slot(conn, user_id, week, limit)
    return _claim_ai_slot(conn, user_id, week, limit)


def _claim_scan_slot(conn: sqlite3.Connection, user_id: int, week: str, limit: int) -> bool:
    row = conn.execute(
        "SELECT scans FROM weekly_usage WHERE user_id = ? AND week_start = ?",
        (user_id, week),
    ).fetchone()
    if row is not None and int(row["scans"]) >= limit:
        return False
    if row is None:
        conn.execute(
            "INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs) VALUES (?, ?, 1, 0)",
            (user_id, week),
        )
    else:
        conn.execute(
            "UPDATE weekly_usage SET scans = scans + 1 WHERE user_id = ? AND week_start = ?",
            (user_id, week),
        )
    return True


def _claim_ai_slot(conn: sqlite3.Connection, user_id: int, week: str, limit: int) -> bool:
    row = conn.execute(
        "SELECT ai_msgs FROM weekly_usage WHERE user_id = ? AND week_start = ?",
        (user_id, week),
    ).fetchone()
    if row is not None and int(row["ai_msgs"]) >= limit:
        return False
    if row is None:
        conn.execute(
            "INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs) VALUES (?, ?, 0, 1)",
            (user_id, week),
        )
    else:
        conn.execute(
            "UPDATE weekly_usage SET ai_msgs = ai_msgs + 1 WHERE user_id = ? AND week_start = ?",
            (user_id, week),
        )
    return True


def _claim_bonus(conn: sqlite3.Connection, user_id: int, *, scan: bool) -> bool:
    """Spend one bonus unit atomically; False when the balance is empty."""
    if scan:
        cursor = conn.execute(
            "UPDATE users SET bonus_scans = bonus_scans - 1 WHERE user_id = ? AND bonus_scans > 0",
            (user_id,),
        )
    else:
        cursor = conn.execute(
            "UPDATE users SET bonus_ai_msgs = bonus_ai_msgs - 1 WHERE user_id = ? AND bonus_ai_msgs > 0",
            (user_id,),
        )
    return cursor.rowcount > 0


def _consume(conn: sqlite3.Connection, user_id: int, week: str, *, scan: bool, limit: int) -> bool:
    """Consume one unit: weekly allotment first, then the bonus balance."""
    return _claim_week_slot(conn, user_id, week, scan=scan, limit=limit) or _claim_bonus(
        conn, user_id, scan=scan
    )


def increment_usage(user_id: int, scan: bool = False, ai_msg: bool = False) -> bool:
    """Consume 1 scan and/or 1 AI msg; False when no quota is left.

    All-or-nothing: if either unit cannot be claimed the transaction rolls
    back, so no partial consumption is billed.
    """
    week = _current_week_start()
    tier = get_user_tier(user_id)
    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if scan:
            if not _consume(
                conn,
                user_id,
                week,
                scan=True,
                limit=_limit_for(tier, "scans_per_week", DEFAULT_SCANS_PER_WEEK),
            ):
                conn.rollback()
                return False
            conn.execute(
                "UPDATE users SET total_scans = total_scans + 1 WHERE user_id = ?", (user_id,)
            )
        if ai_msg:
            if not _consume(
                conn,
                user_id,
                week,
                scan=False,
                limit=_limit_for(tier, "ai_msgs_per_week", DEFAULT_AI_MSGS_PER_WEEK),
            ):
                conn.rollback()
                return False
            conn.execute(
                "UPDATE users SET total_ai_msgs = total_ai_msgs + 1 WHERE user_id = ?", (user_id,)
            )
        conn.commit()
        return True
    except sqlite3.Error:
        conn.rollback()
        logger.warning("usage_increment_failed user_id=%s", user_id, exc_info=True)
        return False
    finally:
        conn.close()


def check_rate_limit(user_id: int, action: str = QUOTA_SCAN) -> tuple[bool, int, int]:
    """Return ``(allowed, current_used, max_allowed)`` — an advisory read.

    The authoritative check is the atomic claim in :func:`increment_usage`.
    """
    tier = get_user_tier(user_id)
    scans_used, ai_used = get_weekly_usage(user_id)
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT bonus_scans, bonus_ai_msgs FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    if action == QUOTA_SCAN:
        limit = _limit_for(tier, "scans_per_week", DEFAULT_SCANS_PER_WEEK)
        current, bonus = scans_used, (user["bonus_scans"] if user else 0)
    else:
        limit = _limit_for(tier, "ai_msgs_per_week", DEFAULT_AI_MSGS_PER_WEEK)
        current, bonus = ai_used, (user["bonus_ai_msgs"] if user else 0)
    return (current < limit) or (bonus > 0), current, limit
