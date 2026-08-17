#!/usr/bin/env python3
"""User management and user-account data for the rugmunchbot DB layer.

Holds every user-keyed / account-level helper that is not tier, watchlist,
stars or alert specific: user CRUD, API tokens (hashed at rest), scan history,
user stats, bot-state toggles, admin audit log, daily activity stats, AI chat
history and scam reports.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from ._base import get_db
from .tiers import get_weekly_usage

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
        rows = conn.execute("SELECT id, token FROM api_tokens WHERE token IS NOT NULL AND token != ''").fetchall()
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


def get_or_create_user(user_id: int, username: str | None = None, first_name: str | None = None) -> dict[str, Any]:
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


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    """Return a full user row (or None) — used by /admin_user."""
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def set_banned(user_id: int, banned: bool) -> bool:
    """Ban/unban a user. Returns True when the user exists."""
    conn = get_db()
    cur = conn.execute("UPDATE users SET is_banned = ? WHERE user_id = ?", (1 if banned else 0, user_id))
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


# ── Scan History ──


def log_scan(user_id: int, token_address: str, chain: str, scan_type: str, risk_score: float) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO scan_history (user_id, token_address, chain, scan_type, risk_score, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, token_address, chain, scan_type, risk_score, int(time.time())),
    )
    conn.commit()
    conn.close()


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
    rows = conn.execute("SELECT * FROM admin_logs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
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
    start_of_day = int(datetime.combine(datetime.now(UTC).date(), datetime.min.time(), tzinfo=UTC).timestamp())
    conn = get_db()
    c = conn.execute("SELECT COUNT(*) as c FROM users WHERE last_active >= ?", (start_of_day,)).fetchone()["c"]
    conn.close()
    return c


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


# ── Scam reports (v6) ──


def report_scam(user_id: int, token_address: str, chain: str | None = None, reason: str = "") -> bool:
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
