"""Sqlite persistence for group call tracking (first caller wins)."""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from rugmunchbot.db import get_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    username TEXT,
    token_address TEXT NOT NULL,
    address_key TEXT NOT NULL,
    chain TEXT,
    note TEXT,
    called_at INTEGER NOT NULL,
    UNIQUE(chat_id, address_key)
);
CREATE INDEX IF NOT EXISTS idx_calls_chat_time ON calls(chat_id, called_at DESC);
"""


def init_calls_schema(conn: sqlite3.Connection | None = None) -> None:
    """Create the call table; idempotent, safe on every startup."""
    db = conn or get_db()
    db.executescript(SCHEMA)
    db.commit()


def record_call(
    chat_id: int,
    user_id: int,
    username: str | None,
    token_address: str,
    chain: str = "",
    note: str = "",
    conn: sqlite3.Connection | None = None,
    now: int | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Insert a call; returns (stored, row). An existing call is never replaced."""
    db = conn or get_db()
    ts = int(now if now is not None else time.time())
    try:
        db.execute(
            "INSERT INTO calls (chat_id, user_id, username, token_address, address_key,"
            " chain, note, called_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, username, token_address, token_address.lower(), chain, note, ts),
        )
        db.commit()
        stored = True
    except sqlite3.IntegrityError:
        stored = False
    row = first_call(chat_id, token_address, conn=db)
    return stored, row or {}


def first_call(
    chat_id: int, token_address: str, conn: sqlite3.Connection | None = None
) -> dict[str, Any] | None:
    db = conn or get_db()
    cur = db.execute(
        "SELECT chat_id, user_id, username, token_address, chain, note, called_at"
        " FROM calls WHERE chat_id = ? AND address_key = ?",
        (chat_id, token_address.lower()),
    )
    row = cur.fetchone()
    return _row(row) if row else None


def group_calls(
    chat_id: int, limit: int = 20, conn: sqlite3.Connection | None = None
) -> list[dict[str, Any]]:
    db = conn or get_db()
    cur = db.execute(
        "SELECT chat_id, user_id, username, token_address, chain, note, called_at"
        " FROM calls WHERE chat_id = ? ORDER BY called_at DESC LIMIT ?",
        (chat_id, limit),
    )
    return [_row(r) for r in cur.fetchall()]


def leaderboard(
    chat_id: int,
    since_ts: int = 0,
    limit: int = 10,
    conn: sqlite3.Connection | None = None,
) -> list[dict[str, Any]]:
    """Rank callers by calls logged. Counts only — never an invented return."""
    db = conn or get_db()
    cur = db.execute(
        "SELECT user_id, max(COALESCE(username, '')) AS username,"
        " count(*) AS calls, max(called_at) AS last_call"
        " FROM calls WHERE chat_id = ? AND called_at >= ?"
        " GROUP BY user_id ORDER BY calls DESC, last_call DESC LIMIT ?",
        (chat_id, since_ts, limit),
    )
    return [
        {"user_id": r[0], "username": r[1] or None, "calls": r[2], "last_call": r[3]}
        for r in cur.fetchall()
    ]


def _row(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "chat_id": row[0],
        "user_id": row[1],
        "username": row[2],
        "token_address": row[3],
        "chain": row[4] or "",
        "note": row[5] or "",
        "called_at": row[6],
    }
