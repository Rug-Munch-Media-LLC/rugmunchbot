"""Stars ledger + watchlist cap atomicity, idempotent upserts, dedupe keys."""

from __future__ import annotations

import itertools
import time

from _db_helpers import count_star_transactions, run_concurrently, seed_user


def test_concurrent_spend_never_drives_balance_negative(sqlite_db):
    """20 threads spending from a 5-star balance: exactly 5 spends succeed."""
    seed_user(sqlite_db, 1)
    sqlite_db.add_stars(1, 5, "purchase")
    results = run_concurrently(lambda: sqlite_db.spend_stars(1, 1, "scan"), 20)
    assert results.count(True) == 5
    assert sqlite_db.get_user_by_id(1)["stars_balance"] == 0
    assert count_star_transactions(sqlite_db, 1, "scan") == 5


def test_spend_ledger_not_written_when_balance_is_short(sqlite_db):
    seed_user(sqlite_db, 2)
    assert sqlite_db.spend_stars(2, 10, "scan") is False
    assert count_star_transactions(sqlite_db, 2, "scan") == 0
    assert sqlite_db.get_user_by_id(2)["stars_balance"] == 0


def test_concurrent_top_up_cannot_overdraw_stars(sqlite_db):
    """10 stars, cost 3 per pack: exactly 3 top-ups succeed (balance 1)."""
    seed_user(sqlite_db, 3)
    sqlite_db.add_stars(3, 10, "purchase")
    results = run_concurrently(lambda: sqlite_db.apply_top_up(3, "scans", 5, 3), 10)
    assert results.count(True) == 3
    user = sqlite_db.get_user_by_id(3)
    assert user["stars_balance"] == 1
    assert user["bonus_scans"] == 15


def test_watchlist_cap_is_atomic(sqlite_db):
    """15 threads adding to a 10-slot tier: exactly 10 items land."""
    seed_user(sqlite_db, 4, tier="scout", expires=int(time.time()) + 3600)
    counter = itertools.count()
    results = run_concurrently(
        lambda: sqlite_db.add_watchlist(4, f"0xw{next(counter)}", "base"),
        15,
    )
    assert results.count(True) == 10
    assert len(sqlite_db.get_watchlist(4)) == 10


def test_free_tier_has_no_watchlist_slots(sqlite_db):
    seed_user(sqlite_db, 5)
    assert sqlite_db.add_watchlist(5, "0x1", "base") is False
    assert sqlite_db.get_watchlist(5) == []


def test_bot_state_upsert_is_idempotent(sqlite_db):
    sqlite_db.set_bot_state("bot_enabled", "on")
    sqlite_db.set_bot_state("bot_enabled", "off")
    assert sqlite_db.get_bot_state("bot_enabled") == "off"
    conn = sqlite_db.get_db()
    try:
        rows = conn.execute(
            "SELECT COUNT(*) AS c FROM bot_state WHERE key = 'bot_enabled'"
        ).fetchone()
    finally:
        conn.close()
    assert rows["c"] == 1


def test_daily_counters_accumulate_on_one_row(sqlite_db):
    sqlite_db.record_activity(msg=True)
    sqlite_db.record_activity(msg=True, cmd=True, tokens=7)
    stats = sqlite_db.get_daily_stats()
    assert (stats["messages"], stats["commands"], stats["ai_tokens"]) == (2, 1, 7)


def test_kol_call_dedupe_key_is_tweet_id(sqlite_db):
    assert sqlite_db.record_kol_call(handle="@Alpha", tweet_id="111", token_symbol="ABC") is True
    assert sqlite_db.record_kol_call(handle="@Alpha", tweet_id="111", token_symbol="XYZ") is True
    conn = sqlite_db.get_db()
    try:
        rows = conn.execute("SELECT token_symbol FROM kol_calls WHERE tweet_id = '111'").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["token_symbol"] == "XYZ"
