#!/usr/bin/env python3
"""
CryptoRugMunch Bot - Database Layer
====================================
SQLite-based user tracking, subscriptions, scan counts, watchlists.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import logging
import os
import sqlite3
import tempfile
import time
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_db_path() -> str:
    """Resolve the bot SQLite path to a writable location.

    Priority:
      1. BOT_DB_PATH env override.
      2. Package-local data dir (default) — used when writable.
      3. Temp dir fallback for read-only containers (/app is read-only).
    """
    override = os.getenv("BOT_DB_PATH", "").strip()
    if override:
        return override
    default = os.path.join(os.path.dirname(__file__), "data", "bot_users.db")
    try:
        os.makedirs(os.path.dirname(default), exist_ok=True)
        return default
    except OSError:
        fallback_dir = os.path.join(tempfile.gettempdir(), "rmi", "telegram")
        os.makedirs(fallback_dir, exist_ok=True)
        return os.path.join(fallback_dir, "bot_users.db")


DB_PATH = _resolve_db_path()


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
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
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            url TEXT NOT NULL,
            event_type TEXT DEFAULT 'all',
            backend_id TEXT,
            is_active INTEGER DEFAULT 1,
            last_ping TEXT,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            token TEXT UNIQUE NOT NULL,
            token_prefix TEXT DEFAULT '',
            name TEXT DEFAULT 'default',
            is_active INTEGER DEFAULT 1,
            last_used INTEGER,
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_weekly_user ON weekly_usage(user_id, week_start);
        CREATE INDEX IF NOT EXISTS idx_watchlist_user ON watchlists(user_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_scan_user ON scan_history(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_webhooks_user ON webhooks(user_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id, is_active);

        -- v5: bot-wide feature toggles (e.g. bot_enabled, chat_enabled)
        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- v5: admin action audit log
        CREATE TABLE IF NOT EXISTS admin_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            details TEXT,
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_admin_logs_ts ON admin_logs(created_at);

        -- v5: daily activity counters (messages, commands, RAG queries, AI tokens)
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            messages INTEGER DEFAULT 0,
            commands INTEGER DEFAULT 0,
            rag_queries INTEGER DEFAULT 0,
            ai_tokens INTEGER DEFAULT 0
        );

        -- v5: per-user AI chat history (conversation context, last N messages)
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history(user_id, created_at);

        -- v6: user-submitted scam reports (feeds the known-scam denylist)
        CREATE TABLE IF NOT EXISTS scam_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            chain TEXT,
            reason TEXT,
            status TEXT DEFAULT 'open',
            created_at INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_scam_reports_addr ON scam_reports(token_address);
        CREATE INDEX IF NOT EXISTS idx_scam_reports_user ON scam_reports(user_id, created_at);
    """)
    # Migration: add bonus columns if they don't exist (SQLite safe)
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE users ADD COLUMN bonus_scans INTEGER DEFAULT 0")
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE users ADD COLUMN bonus_ai_msgs INTEGER DEFAULT 0")
    # Migration: alert_triggered_at for tracking when alerts fire
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE watchlists ADD COLUMN alert_triggered_at TEXT")
    # Migration: non-sensitive display prefix for api_tokens (hashed at rest)
    with contextlib.suppress(sqlite3.OperationalError):
        conn.execute("ALTER TABLE api_tokens ADD COLUMN token_prefix TEXT DEFAULT ''")
    conn.commit()
    conn.close()
    # One-shot upgrade: hash any legacy plaintext api_tokens.token values.
    try:
        migrated = migrate_plaintext_api_tokens()
        if migrated:
            logger.info("api_tokens_migrated_plaintext count=%d", migrated)
    except Exception:
        logger.warning("api_tokens_migration_failed", exc_info=True)


# ── User Management ──


# ── API tokens (hashed at rest) ──

API_TOKEN_PREFIX_LEN = 12


def hash_api_token(raw_token: str) -> str:
    """Return the hex sha256 digest of a raw API token — the value stored at rest."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def token_prefix(raw_token: str, length: int = API_TOKEN_PREFIX_LEN) -> str:
    """Non-sensitive display prefix (first ``length`` chars) for /api list.

    Never used for authentication — only to keep the list UX working without
    exposing the raw secret.
    """
    return raw_token[:length]


def _is_hashed_token(stored: str) -> bool:
    """True when ``stored`` is a 64-char lowercase hex sha256 digest (not plaintext)."""
    return len(stored) == 64 and all(c in "0123456789abcdef" for c in stored)


def verify_api_token(raw_token: str) -> dict[str, Any] | None:
    """Verify a presented raw token against stored sha256 digests.

    Hashes the presented token and compares against every active stored digest
    with hmac.compare_digest (constant time). Returns the matching token row
    dict, or None when no active token matches. Non-hashed (legacy plaintext)
    rows are never matched — run migrate_plaintext_api_tokens() first.
    """
    if not raw_token:
        return None
    presented_hash = hash_api_token(raw_token)
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM api_tokens WHERE is_active = 1").fetchall()
    finally:
        conn.close()
    for row in rows:
        stored = row["token"]
        if stored and _is_hashed_token(stored) and hmac.compare_digest(stored, presented_hash):
            return dict(row)
    return None


def migrate_plaintext_api_tokens() -> int:
    """One-shot upgrade: hash any plaintext api_tokens.token values.

    Detects rows whose stored token is NOT a 64-char hex digest (i.e. the old
    ``rmi_*`` plaintext format), replaces the value with sha256(token) and
    stores the first ``API_TOKEN_PREFIX_LEN`` chars as a non-sensitive
    ``token_prefix``. Returns the number of rows migrated.

    Idempotent: once a row is hashed it is left untouched on later runs, so
    this is safe to call on every bot start.
    """
    conn = get_db()
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(api_tokens)").fetchall()]
        if "token_prefix" not in cols:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ALTER TABLE api_tokens ADD COLUMN token_prefix TEXT DEFAULT ''")
                conn.commit()
        rows = conn.execute(
            "SELECT id, token FROM api_tokens WHERE token IS NOT NULL AND token != ''"
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return 0  # api_tokens table doesn't exist yet

    migrated = 0
    for row in rows:
        stored = row["token"]
        if _is_hashed_token(stored):
            continue
        # Legacy plaintext — capture a prefix before overwriting the raw secret.
        prefix = token_prefix(stored)
        conn.execute(
            "UPDATE api_tokens SET token = ?, token_prefix = ? WHERE id = ?",
            (hash_api_token(stored), prefix, row["id"]),
        )
        migrated += 1
    if migrated:
        conn.commit()
    conn.close()
    return migrated


# ── User Management ──


def get_or_create_user(
    user_id: int, username: str | None = None, first_name: str | None = None
) -> dict[str, Any]:
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
    from rugmunchbot.config import BETA_MODE

    if BETA_MODE:
        return "pro"  # Beta: all features unlocked for everyone
    conn = get_db()
    row = conn.execute(
        "SELECT tier, tier_expires FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return "free"
    if row["tier"] != "free" and row["tier_expires"] > 0 and time.time() > row["tier_expires"]:
        conn = get_db()
        conn.execute(
            "UPDATE users SET tier = 'free', tier_expires = 0 WHERE user_id = ?", (user_id,)
        )
        conn.commit()
        conn.close()
        return "free"
    return row["tier"]  # type: ignore[no-any-return]  # httpx/sqlite JSON Any access (ported verbatim)


def set_user_tier(user_id: int, tier: str, days: int = 30) -> None:
    expires = int(time.time() + timedelta(days=days).total_seconds()) if tier != "free" else 0
    conn = get_db()
    conn.execute(
        "UPDATE users SET tier = ?, tier_expires = ? WHERE user_id = ?", (tier, expires, user_id)
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
        user = conn.execute(
            "SELECT bonus_scans FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        from rugmunchbot.config import TIERS

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
            conn.execute(
                "UPDATE users SET bonus_scans = bonus_scans - 1 WHERE user_id = ?", (user_id,)
            )
        else:
            conn.close()
            return False  # No quota available

        conn.execute("UPDATE users SET total_scans = total_scans + 1 WHERE user_id = ?", (user_id,))

    if ai_msg:
        row = conn.execute(
            "SELECT ai_msgs FROM weekly_usage WHERE user_id = ? AND week_start = ?", (user_id, week)
        ).fetchone()
        user = conn.execute(
            "SELECT bonus_ai_msgs FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        from rugmunchbot.config import TIERS

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
            conn.execute(
                "UPDATE users SET bonus_ai_msgs = bonus_ai_msgs - 1 WHERE user_id = ?", (user_id,)
            )
        else:
            conn.close()
            return False

        conn.execute(
            "UPDATE users SET total_ai_msgs = total_ai_msgs + 1 WHERE user_id = ?", (user_id,)
        )

    conn.commit()
    conn.close()
    return True


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


def get_active_alerts() -> list[dict[str, Any]]:
    """Return all watchlist entries with active price alerts."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM watchlists
        WHERE alert_price IS NOT NULL
          AND alert_direction IS NOT NULL
          AND is_active = 1
        ORDER BY created_at DESC
        """,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alert_triggered(watch_id: int) -> None:
    """Mark an alert as fired by setting triggered_at and deactivating."""
    conn = get_db()
    conn.execute(
        "UPDATE watchlists SET is_active = 0, alert_triggered_at = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), watch_id),
    )
    conn.commit()
    conn.close()


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


# ── Scan History ──


def log_scan(
    user_id: int, token_address: str, chain: str, scan_type: str, risk_score: float
) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO scan_history (user_id, token_address, chain, scan_type, risk_score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, token_address, chain, scan_type, risk_score, int(time.time())),
    )
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


# ── Stars Balance ──


def add_stars(user_id: int, amount: int, action: str, payment_id: str | None = None) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE users SET stars_balance = stars_balance + ? WHERE user_id = ?", (amount, user_id)
    )
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
    conn.execute(
        "UPDATE users SET stars_balance = stars_balance - ? WHERE user_id = ?", (amount, user_id)
    )
    conn.execute(
        "INSERT INTO star_transactions (user_id, amount, action, created_at) VALUES (?, ?, ?, ?)",
        (user_id, -amount, action, int(time.time())),
    )
    conn.commit()
    conn.close()
    return True


def apply_top_up(
    user_id: int, pack_type: str, amount: int, stars_cost: int, payment_id: str | None = None
) -> bool:
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
        conn.execute(
            "UPDATE users SET bonus_scans = bonus_scans + ? WHERE user_id = ?", (amount, user_id)
        )
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


def get_user_stats(user_id: int) -> dict[str, Any]:
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


# ── Bot state toggles (v5) ──


def set_bot_state(key: str, value: str) -> None:
    """Persist a bot-wide toggle (e.g. bot_enabled=on|off, chat_enabled=on|off)."""
    conn = get_db()
    conn.execute(
        "INSERT INTO bot_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_bot_state(key: str, default: str = "on") -> str:
    """Read a bot-wide toggle, returning ``default`` when unset."""
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return row["value"] if row else default


# ── Admin action audit log (v5) ──


def log_admin_action(admin_id: int, action: str, details: str = "") -> None:
    """Append an entry to the admin audit log (every admin command logs here)."""
    conn = get_db()
    conn.execute(
        "INSERT INTO admin_logs (admin_id, action, details, created_at) VALUES (?, ?, ?, ?)",
        (admin_id, action, details[:500], int(time.time())),
    )
    conn.commit()
    conn.close()


def get_admin_logs(limit: int = 20) -> list[dict[str, Any]]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM admin_logs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Daily activity stats (v5) ──


def _today_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def record_activity(
    msg: bool = False,
    cmd: bool = False,
    rag: bool = False,
    tokens: int = 0,
) -> None:
    """Increment daily counters (messages / commands / RAG queries / AI tokens)."""
    date = _today_str()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO daily_stats (date, messages, commands, rag_queries, ai_tokens)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            messages = messages + excluded.messages,
            commands = commands + excluded.commands,
            rag_queries = rag_queries + excluded.rag_queries,
            ai_tokens = ai_tokens + excluded.ai_tokens
        """,
        (date, 1 if msg else 0, 1 if cmd else 0, 1 if rag else 0, tokens),
    )
    conn.commit()
    conn.close()


def get_daily_stats(date: str | None = None) -> dict[str, Any]:
    """Return daily counters for ``date`` (default: today) as a dict with 0 defaults."""
    date = date or _today_str()
    conn = get_db()
    row = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (date,)).fetchone()
    conn.close()
    if not row:
        return {"date": date, "messages": 0, "commands": 0, "rag_queries": 0, "ai_tokens": 0}
    return dict(row)


def count_active_today() -> int:
    """Number of users active since the start of today (UTC)."""
    start_of_day = int(
        datetime.combine(datetime.now(UTC).date(), datetime.min.time(), tzinfo=UTC).timestamp()
    )
    conn = get_db()
    c = conn.execute(
        "SELECT COUNT(*) as c FROM users WHERE last_active >= ?", (start_of_day,)
    ).fetchone()["c"]
    conn.close()
    return c  # type: ignore[no-any-return]  # httpx/sqlite JSON Any access (ported verbatim)


# ── AI chat history / conversation context (v5) ──

CHAT_HISTORY_KEEP = 50  # max stored rows per user (prune older)
CHAT_HISTORY_CONTEXT = 5  # last N messages injected into the LLM


def add_chat_message(user_id: int, role: str, content: str) -> None:
    """Store one user/assistant message for conversation context."""
    if not content or not content.strip():
        return
    conn = get_db()
    conn.execute(
        "INSERT INTO chat_history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content.strip()[:2000], int(time.time())),
    )
    # Keep only the most recent CHAT_HISTORY_KEEP rows per user.
    conn.execute(
        "DELETE FROM chat_history WHERE user_id = ? AND id NOT IN "
        "(SELECT id FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
        (user_id, user_id, CHAT_HISTORY_KEEP),
    )
    conn.commit()
    conn.close()


def get_chat_history(user_id: int, limit: int = CHAT_HISTORY_CONTEXT) -> list[dict[str, Any]]:
    """Return the last ``limit`` (role, content) pairs, oldest-first."""
    conn = get_db()
    rows = conn.execute(
        "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def clear_chat_history(user_id: int) -> None:
    conn = get_db()
    conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# ── Admin user lookup (v5) ──


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Return a full user row (or None) — used by /admin_user."""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_banned(user_id: int, banned: bool) -> bool:
    """Ban/unban a user. Returns True when the user exists."""
    conn = get_db()
    cur = conn.execute(
        "UPDATE users SET is_banned = ? WHERE user_id = ?", (1 if banned else 0, user_id)
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def is_user_banned(user_id: int) -> bool:
    """Return True when the user is flagged banned in the local SQLite DB.

    Fast single-column read used by the central ban gate (bot.py
    ``install_ban_gate``). Unknown users are treated as not banned.
    """
    conn = get_db()
    try:
        row = conn.execute("SELECT is_banned FROM users WHERE user_id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    return bool(row and row["is_banned"])


# ── Scam reports (v6) ──


def report_scam(
    user_id: int, token_address: str, chain: str | None = None, reason: str = ""
) -> bool:
    """Record a user-submitted scam report (feeds the known-scam denylist).

    Returns True when the report was stored. The address becomes a known
    scam address for the whole fleet (get_known_scam_addresses).
    """
    addr = (token_address or "").strip()
    if not addr:
        return False
    conn = get_db()
    conn.execute(
        "INSERT INTO scam_reports (user_id, token_address, chain, reason, status, created_at) "
        "VALUES (?, ?, ?, ?, 'open', ?)",
        (user_id, addr, chain or "", (reason or "")[:300], int(time.time())),
    )
    conn.commit()
    conn.close()
    return True


def get_known_scam_addresses() -> set[str]:
    """Return the set of distinct lowercased addresses with open scam reports."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT lower(token_address) AS addr FROM scam_reports WHERE status = 'open'"
        ).fetchall()
    finally:
        conn.close()
    return {r["addr"] for r in rows if r["addr"]}


def list_scam_reports(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent scam reports (newest first)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM scam_reports ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# Init on import
try:
    init_db()
except Exception:
    # Router must still mount if the DB is unavailable (e.g. read-only
    # filesystem) — handlers degrade gracefully per-request.
    logger.warning("bot_db_init_failed", exc_info=True)
