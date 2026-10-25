"""Stars-paid monitor windows, slot caps and restart persistence."""

from __future__ import annotations

import itertools
import os
import sqlite3
import subprocess
import sys
import time

from _db_helpers import run_concurrently


def _insert_legacy_monitor(db, user_id: int, address: str, expires_at) -> None:
    conn = db.get_db()
    try:
        conn.execute(
            "INSERT INTO monitors (user_id, token_address, chain, baseline_json, created_at, expires_at, is_active) "
            "VALUES (?, ?, 'base', '{}', 0, ?, 1)",
            (user_id, address, expires_at),
        )
        conn.commit()
    finally:
        conn.close()


def test_claim_always_writes_a_window(sqlite_db):
    assert (
        sqlite_db.claim_monitor_slot(1, "0xABC", sqlite_db.MonitorSpec(chain="ethereum"), days=7)
        is True
    )
    monitor = sqlite_db.get_monitors(1)[0]
    assert monitor["token_address"] == "0xabc"
    assert monitor["expires_at"] is not None
    assert monitor["expires_at"] > int(time.time()) + 6 * 86400


def test_claim_refuses_an_unbounded_window(sqlite_db):
    assert (
        sqlite_db.claim_monitor_slot(2, "0xdef", sqlite_db.MonitorSpec(chain="ethereum"), days=0)
        is False
    )
    assert (
        sqlite_db.claim_monitor_slot(2, "0xdef", sqlite_db.MonitorSpec(chain="ethereum"), days=-30)
        is False
    )
    assert sqlite_db.get_monitors(2) == []


def test_null_window_monitor_fails_closed(sqlite_db):
    _insert_legacy_monitor(sqlite_db, 3, "0xnull", None)
    assert sqlite_db.get_active_monitors() == []
    assert sqlite_db.expire_monitors() == 1
    assert sqlite_db.get_monitors(3) == []


def test_zero_window_monitor_fails_closed(sqlite_db):
    _insert_legacy_monitor(sqlite_db, 4, "0xzero", 0)
    assert sqlite_db.get_active_monitors() == []
    assert sqlite_db.expire_monitors() == 1


def test_expired_monitor_is_not_delivered(sqlite_db):
    assert (
        sqlite_db.claim_monitor_slot(5, "0xold", sqlite_db.MonitorSpec(chain="base"), days=1)
        is True
    )
    conn = sqlite_db.get_db()
    try:
        conn.execute(
            "UPDATE monitors SET expires_at = ? WHERE user_id = 5", (int(time.time()) - 5,)
        )
        conn.commit()
    finally:
        conn.close()
    assert sqlite_db.get_active_monitors() == []
    assert sqlite_db.expire_monitors() == 1
    assert sqlite_db.get_monitors(5) == []


def test_open_window_monitor_is_delivered(sqlite_db):
    assert (
        sqlite_db.claim_monitor_slot(
            6, "0xlive", sqlite_db.MonitorSpec(chain="base", baseline={"score": 12}), days=30
        )
        is True
    )
    active = sqlite_db.get_active_monitors()
    assert [m["token_address"] for m in active] == ["0xlive"]
    assert active[0]["baseline"] == {"score": 12}


def test_duplicate_claim_is_rejected(sqlite_db):
    assert sqlite_db.claim_monitor_slot(7, "0xDUP", sqlite_db.MonitorSpec(chain="base")) is True
    assert sqlite_db.claim_monitor_slot(7, "0xdup", sqlite_db.MonitorSpec(chain="base")) is False
    assert len(sqlite_db.get_monitors(7)) == 1


def test_concurrent_claims_respect_the_slot_cap(sqlite_db):
    """20 threads racing for 5 slots: exactly 5 claims succeed."""
    counter = itertools.count()
    results = run_concurrently(
        lambda: sqlite_db.claim_monitor_slot(
            8, f"0xt{next(counter)}", sqlite_db.MonitorSpec(chain="base"), max_active=5
        ),
        20,
    )
    assert results.count(True) == 5
    assert len(sqlite_db.get_monitors(8)) == 5


def test_monitor_survives_a_process_restart(sqlite_db):
    assert (
        sqlite_db.claim_monitor_slot(9, "0xrestart", sqlite_db.MonitorSpec(chain="base"), days=30)
        is True
    )
    script = (
        "import os, sqlite3\n"
        "conn = sqlite3.connect(os.environ['BOT_DB_PATH'])\n"
        "row = conn.execute("
        "'SELECT COUNT(*) FROM monitors WHERE user_id = 9 AND expires_at > 0 AND is_active = 1'"
        ").fetchone()\n"
        "print(row[0])\n"
    )
    env = {**os.environ, "BOT_DB_PATH": str(sqlite_db.DB_PATH)}
    out = subprocess.run(  # noqa: S603 - fixed argv, no shell, test-only interpreter
        [sys.executable, "-c", script], capture_output=True, text=True, env=env, timeout=60
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "1"
    conn = sqlite3.connect(sqlite_db.DB_PATH)
    try:
        assert conn.execute("SELECT expires_at FROM monitors WHERE user_id = 9").fetchone()[0] > 0
    finally:
        conn.close()
