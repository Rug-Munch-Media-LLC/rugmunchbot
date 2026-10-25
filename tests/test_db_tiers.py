"""Tier resolution + expiry windows fail closed (isolated SQLite)."""

from __future__ import annotations

import time

import pytest
from _db_helpers import seed_user

FREE_SCANS = 25


def test_paid_tier_with_null_window_fails_closed(sqlite_db):
    seed_user(sqlite_db, 1, tier="pro", expires=None)
    assert sqlite_db.get_user_tier(1) == "free"
    assert sqlite_db.get_user_by_id(1)["tier_expires"] == 0


def test_paid_tier_with_zero_window_fails_closed(sqlite_db):
    seed_user(sqlite_db, 2, tier="hunter", expires=0)
    assert sqlite_db.get_user_tier(2) == "free"


def test_expired_window_returns_free(sqlite_db):
    seed_user(sqlite_db, 3, tier="scout", expires=int(time.time()) - 60)
    assert sqlite_db.get_user_tier(3) == "free"


def test_open_window_keeps_paid_tier(sqlite_db):
    seed_user(sqlite_db, 4, tier="scout", expires=int(time.time()) + 3600)
    assert sqlite_db.get_user_tier(4) == "scout"


def test_activate_tier_writes_a_bounded_window(sqlite_db):
    seed_user(sqlite_db, 5)
    sqlite_db.activate_tier(5, "pro", 7)
    row = sqlite_db.get_user_by_id(5)
    assert row["tier"] == "pro"
    assert row["tier_expires"] > int(time.time()) + 6 * 86400


@pytest.mark.parametrize("days", [0, -1])
def test_paid_tier_refuses_an_unbounded_window(sqlite_db, days):
    seed_user(sqlite_db, 6)
    with pytest.raises(ValueError, match="window must be"):
        sqlite_db.set_user_tier(6, "pro", days)
    with pytest.raises(ValueError, match="window must be"):
        sqlite_db.activate_tier(6, "pro", days)
    assert sqlite_db.get_user_by_id(6)["tier"] == "free"
