"""Paid monitors carry an expiry on INSERT, and expiry is enforced on read.

A monitor is a Stars-paid window, so the row must be born with ``expires_at``;
a row inserted with a NULL window would be a free permanent monitor. The
earlier lane reported this as broken and could not reproduce it — this test
pins the behaviour so a future schema/CRUD edit cannot quietly drop the column.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from rugmunchbot import db

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

TOKEN = "So11111111111111111111111111111111111111112"


@pytest.fixture
def temp_db(tmp_path: Path) -> Iterator[None]:
    """Point the DB seam at a throwaway file (restored afterwards)."""
    original = db.DB_PATH
    db.DB_PATH = str(tmp_path / "bot.db")
    db.init_db()
    try:
        yield
    finally:
        db.DB_PATH = original


def test_add_monitor_writes_expires_at_on_insert(temp_db: None) -> None:
    before = int(time.time())
    assert db.add_monitor(4242, TOKEN, "solana", symbol="RUG") is True
    after = int(time.time())

    row = db.get_monitors(4242)[0]

    assert row["expires_at"] is not None, "a paid monitor must expire"
    assert row["created_at"] is not None
    assert row["expires_at"] - row["created_at"] == db.MONITOR_DEFAULT_DAYS * 86400
    assert before <= row["created_at"] <= after
    assert row["token_address"] == TOKEN.lower()


def test_custom_window_and_duplicate_insert(temp_db: None) -> None:
    assert db.add_monitor(4242, TOKEN, "solana", days=7) is True
    row = db.get_monitors(4242)[0]
    assert row["expires_at"] - row["created_at"] == 7 * 86400

    assert db.add_monitor(4242, TOKEN, "solana") is False, "one active row per token"
    assert len(db.get_monitors(4242)) == 1


def test_expired_window_is_inactive_and_pruned(temp_db: None) -> None:
    assert db.add_monitor(4242, TOKEN, "solana", days=-1) is True

    assert db.get_active_monitors() == [], "an expired window is not active"
    assert db.expire_monitors() == 1
    assert db.get_monitors(4242) == []
