"""Weekly scan/AI quota claims are atomic and never overspend."""

from __future__ import annotations

from _db_helpers import count_weekly_rows, free_tier_scans, run_concurrently, seed_user, set_bonus

FREE_SCANS = 25


def test_weekly_quota_stops_at_limit(sqlite_db):
    seed_user(sqlite_db, 10)
    results = [sqlite_db.increment_usage(10, scan=True) for _ in range(FREE_SCANS + 5)]
    assert results.count(True) == FREE_SCANS
    assert sqlite_db.get_weekly_usage(10)[0] == FREE_SCANS


def test_concurrent_scan_claims_never_exceed_limit(sqlite_db):
    """40 threads racing for 25 weekly scans: exactly 25 are granted."""
    seed_user(sqlite_db, 11)
    results = run_concurrently(lambda: sqlite_db.increment_usage(11, scan=True), 40)
    assert len(results) == 40
    assert results.count(True) == FREE_SCANS
    assert sqlite_db.get_weekly_usage(11)[0] == FREE_SCANS
    assert sqlite_db.get_user_by_id(11)["total_scans"] == FREE_SCANS


def test_bonus_balance_spent_after_weekly_allotment(sqlite_db):
    seed_user(sqlite_db, 12)
    set_bonus(sqlite_db, 12, scans=3)
    for _ in range(FREE_SCANS):
        assert sqlite_db.increment_usage(12, scan=True) is True
    assert sqlite_db.get_weekly_usage(12)[0] == FREE_SCANS
    assert sqlite_db.increment_usage(12, scan=True) is True
    assert sqlite_db.increment_usage(12, scan=True) is True
    assert sqlite_db.increment_usage(12, scan=True) is True
    assert sqlite_db.increment_usage(12, scan=True) is False
    assert sqlite_db.get_user_by_id(12)["bonus_scans"] == 0


def test_concurrent_bonus_claims_never_go_negative(sqlite_db):
    seed_user(sqlite_db, 13)
    set_bonus(sqlite_db, 13, scans=5)
    with free_tier_scans(0):
        results = run_concurrently(lambda: sqlite_db.increment_usage(13, scan=True), 20)
    assert results.count(True) == 5
    assert sqlite_db.get_user_by_id(13)["bonus_scans"] == 0


def test_ai_quota_is_independent_of_scans(sqlite_db):
    seed_user(sqlite_db, 14)
    assert sqlite_db.increment_usage(14, ai_msg=True) is True
    assert sqlite_db.get_weekly_usage(14) == (0, 1)


def test_week_row_is_a_single_upserted_row(sqlite_db):
    seed_user(sqlite_db, 15)
    for _ in range(3):
        sqlite_db.increment_usage(15, scan=True)
    assert count_weekly_rows(sqlite_db, 15) == 1


def test_claim_without_quota_writes_nothing(sqlite_db):
    seed_user(sqlite_db, 16)
    with free_tier_scans(0):
        assert sqlite_db.increment_usage(16, scan=True) is False
    assert sqlite_db.get_weekly_usage(16) == (0, 0)
    assert sqlite_db.get_user_by_id(16)["total_scans"] == 0
