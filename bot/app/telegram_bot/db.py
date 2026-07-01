#!/usr/bin/env python3
"""
CryptoRugMunch Bot — Database Layer
====================================
SQLite-based user tracking, subscriptions, scan counts, watchlists.
"""

import contextlib
import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta

DB_PATH = os.getenv("BOT_DB_PATH", "/root/backend/data/bot_users.db")


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            tier TEXT DEFAULT 'free',
            tier_expires INTEGER DEFAULT 0,
            total_scans INTEGER DEFAULT 0,
            total_ai_msgs INTEGER DEFAULT 0,
            created_at INTEGER,
            last_active INTEGER,
            is_banned INTEGER DEFAULT 0,
            referral_code TEXT UNIQUE,
            referred_by TEXT,
            stars_balance INTEGER DEFAULT 0,
            bonus_scans INTEGER DEFAULT 0,
            bonus_ai_msgs INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS weekly_usage (
            user_id INTEGER,
            week_start TEXT,
            scans INTEGER DEFAULT 0,
            ai_msgs INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, week_start)
        );
        CREATE TABLE IF NOT EXISTS watchlists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token_address TEXT,
            chain TEXT,
            symbol TEXT,
            alert_price REAL,
            alert_direction TEXT,
            created_at INTEGER,
            is_active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token_address TEXT,
            chain TEXT,
            scan_type TEXT,
            risk_score REAL,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS star_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount INTEGER,
            action TEXT,
            telegram_payment_id TEXT,
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_weekly_user ON weekly_usage(user_id, week_start);
        CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlists(user_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_scan_user ON scan_history(user_id, created_at);
    """)
    # Migration: add bonus columns if they don't exist (SQLite safe)
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE users ADD COLUMN bonus_scans INTEGER DEFAULT 0")
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE users ADD COLUMN bonus_ai_msgs INTEGER DEFAULT 0")
    conn.commit()
    conn.close()


# ── User Management ──


def get_or_create_user(user_id: int, username: str | None = None, first_name: str | None = None) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        conn.execute(
            "UPDATE users SET last_active = ?, username = COALESCE(?, username), first_name = COALESCE(?, first_name) WHERE user_id = ?",
            (int(time.time()), username, first_name, user_id),
        )
        conn.commit()
        result = dict(row)
    else:
        now = int(time.time())
        import secrets

        ref_code = secrets.token_hex(4).upper()
        conn.execute(
            "INSERT INTO users (user_id, username, first_name, created_at, last_active, referral_code) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, now, now, ref_code),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        result = dict(row)
    conn.close()
    return result


def get_user_tier(user_id: int) -> str:
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


def set_user_tier(user_id: int, tier: str, days: int = 30):
    expires = int(time.time() + timedelta(days=days).total_seconds()) if tier != "free" else 0
    conn = get_db()
    conn.execute("UPDATE users SET tier = ?, tier_expires = ? WHERE user_id = ?", (tier, expires, user_id))
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


def increment_usage(user_id: int, scan: bool = False, ai_msg: bool = False):
    """Consume 1 scan or AI msg. Tries weekly allotment first, then bonus balance."""
    week = _current_week_start()
    conn = get_db()

    if scan:
        # Check if weekly allotment has room
        row = conn.execute(
            "SELECT scans FROM weekly_usage WHERE user_id = ? AND week_start = ?", (user_id, week)
        ).fetchone()
        user = conn.execute("SELECT bonus_scans FROM users WHERE user_id = ?", (user_id,)).fetchone()
        from config import TIERS

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
        from config import TIERS

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
    from config import TIERS

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


# ── Watchlist ──


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


def get_watchlist(user_id: int) -> list[dict]:
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


# ── Scan History ──


def log_scan(user_id: int, token_address: str, chain: str, scan_type: str, risk_score: float):
    conn = get_db()
    conn.execute(
        "INSERT INTO scan_history (user_id, token_address, chain, scan_type, risk_score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, token_address, chain, scan_type, risk_score, int(time.time())),
    )
    conn.commit()
    conn.close()


# ── Stars Balance ──


def add_stars(user_id: int, amount: int, action: str, payment_id: str | None = None):
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


# ── Stats ──


def get_user_stats(user_id: int) -> dict:
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    watchlist_count = conn.execute(
        "SELECT COUNT(*) as c FROM watchlists WHERE user_id = ? AND is_active = 1", (user_id,)
    ).fetchone()["c"]
    conn.close()
    if not user:
        return {}
    scans_used, ai_used = get_weekly_usage(user_id)
    return {
        **dict(user),
        "watchlist_count": watchlist_count,
        "scans_this_week": scans_used,
        "ai_msgs_this_week": ai_used,
    }


# Init on import
init_db()
