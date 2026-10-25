"""Schema bootstrap: idempotent up, legacy column backfill, rebuild parity."""

from __future__ import annotations

import sqlite3

from _db_helpers import db_at


def _schema(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
        ).fetchall()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _table_columns(db_path: str, *, users: bool) -> set[str]:
    """Column names for the users or monitors table (PRAGMA is not bindable)."""
    conn = sqlite3.connect(db_path)
    try:
        rows = (
            conn.execute("PRAGMA table_info(users)").fetchall()
            if users
            else conn.execute("PRAGMA table_info(monitors)").fetchall()
        )
    finally:
        conn.close()
    return {r[1] for r in rows}


def test_init_db_is_idempotent(sqlite_db):
    before = _schema(str(sqlite_db.DB_PATH))
    sqlite_db.init_db()
    sqlite_db.init_db()
    assert _schema(str(sqlite_db.DB_PATH)) == before


def test_core_tables_and_indexes_exist(sqlite_db):
    schema = _schema(str(sqlite_db.DB_PATH))
    assert {
        "users",
        "weekly_usage",
        "watchlists",
        "monitors",
        "star_transactions",
        "kol_calls",
        "kol_identities",
    } <= schema
    assert "idx_monitors_user" in schema


def test_init_db_backfills_legacy_user_columns(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, username TEXT, "
            "tier TEXT DEFAULT 'free', tier_expires INTEGER DEFAULT 0)"
        )
        conn.commit()
    finally:
        conn.close()
    with db_at(path) as db:
        db.init_db()
    assert {"bonus_scans", "bonus_ai_msgs"} <= _table_columns(str(path), users=True)


def test_legacy_db_without_monitors_table_gains_a_windowed_one(tmp_path):
    path = tmp_path / "v1.db"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY)")
        conn.commit()
    finally:
        conn.close()
    with db_at(path) as db:
        db.init_db()
        assert "expires_at" in _table_columns(str(path), users=False)
        assert db.claim_monitor_slot(1, "0xabc", db.MonitorSpec(chain="base"), days=1) is True


def test_rebuild_from_scratch_matches_the_original_schema(tmp_path):
    """Down (drop the file) then up (init_db) must yield the same schema."""
    first = tmp_path / "first.db"
    with db_at(first) as db:
        db.init_db()
    expected = _schema(str(first))

    second = tmp_path / "second.db"
    with db_at(second) as db:
        db.init_db()
    assert _schema(str(second)) == expected
