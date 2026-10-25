"""Shared helpers for the DB integrity tests (SQLite only, no network)."""

from __future__ import annotations

import contextlib
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def seed_user(db: Any, user_id: int, tier: str = "free", expires: int | None = 0) -> None:
    """Insert a user row with an explicit tier window (None = NULL window)."""
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO users (user_id, tier, tier_expires, created_at, last_active) VALUES (?, ?, ?, 0, 0)",
            (user_id, tier, expires),
        )
        conn.commit()
    finally:
        conn.close()


def set_bonus(db: Any, user_id: int, scans: int = 0, ai_msgs: int = 0) -> None:
    conn = db.get_db()
    try:
        conn.execute(
            "UPDATE users SET bonus_scans = ?, bonus_ai_msgs = ? WHERE user_id = ?",
            (scans, ai_msgs, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def count_weekly_rows(db: Any, user_id: int) -> int:
    conn = db.get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM weekly_usage WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    return int(row["c"])


def count_star_transactions(db: Any, user_id: int, action: str) -> int:
    conn = db.get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM star_transactions WHERE user_id = ? AND action = ?",
            (user_id, action),
        ).fetchone()
    finally:
        conn.close()
    return int(row["c"])


def run_concurrently(fn: Callable[[], bool], count: int) -> list[bool]:
    """Run ``fn`` in ``count`` threads at once and return every result."""
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        outcome = fn()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


@contextlib.contextmanager
def db_at(path: Any):
    """Point the db shim at ``path`` for the duration of the block."""
    from rugmunchbot import db

    original = db.DB_PATH
    db.DB_PATH = str(path)
    try:
        yield db
    finally:
        db.DB_PATH = original


@contextlib.contextmanager
def free_tier_scans(limit: int):
    """Override the free tier's weekly scan limit for the duration of the block."""
    from rugmunchbot.config import TIERS

    original = TIERS["free"]["scans_per_week"]
    TIERS["free"]["scans_per_week"] = limit
    try:
        yield
    finally:
        TIERS["free"]["scans_per_week"] = original
