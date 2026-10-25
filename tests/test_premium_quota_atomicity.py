"""Quota enforcement under concurrency, expiry and the weekly reset boundary.

The unit of truth is the database, so these tests use a throwaway SQLite file.
The double-spend assertion is a real race: N threads, one shared barrier, and
only the winner may take the last unit. That is the shape of the bug this
replaced — a read-then-write across two connections, where every racer read
"one left" and every racer took it.
"""

from __future__ import annotations

import threading
import time

import pytest

from rugmunchbot import config, db
from rugmunchbot.db._base import get_db, init_db

USER = 4242


@pytest.fixture(autouse=True)
def _billing_mode(monkeypatch):
    """Gating is only testable with beta mode off — otherwise everyone is pro."""
    monkeypatch.setenv("RMI_BOT_BETA_MODE", "false")
    config._resolve_beta_mode.cache_clear()
    yield
    config._resolve_beta_mode.cache_clear()


@pytest.fixture
def bot_db(tmp_path, monkeypatch):
    """A fresh schema per test, pointed at by the supported DB_PATH seam."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bot.db"))
    init_db()
    conn = get_db()
    conn.execute("INSERT INTO users (user_id, tier, tier_expires) VALUES (?, 'free', 0)", (USER,))
    conn.commit()
    conn.close()
    return db


def _activate(tier: str, expires: int) -> None:
    """Set the subscription window with literal SQL — no column interpolation."""
    conn = get_db()
    conn.execute(
        "UPDATE users SET tier = ?, tier_expires = ? WHERE user_id = ?", (tier, expires, USER)
    )
    conn.commit()
    conn.close()


def _set_bonus_scans(value: int) -> None:
    conn = get_db()
    conn.execute("UPDATE users SET bonus_scans = ? WHERE user_id = ?", (value, USER))
    conn.commit()
    conn.close()


def _used() -> tuple[int, int]:
    return db.get_weekly_usage(USER)


def test_free_scans_stop_exactly_at_the_weekly_limit(bot_db) -> None:
    limit = config.TIERS["free"]["scans_per_week"]
    outcomes = [bot_db.increment_usage(USER, scan=True) for _ in range(limit + 3)]
    assert outcomes[:limit] == [True] * limit
    assert outcomes[limit:] == [False] * 3
    assert _used()[0] == limit


def test_one_unit_left_and_eight_racers_yield_one_winner(bot_db) -> None:
    """The regression: concurrent requests must not share the last unit."""
    limit = config.TIERS["free"]["scans_per_week"]
    conn = get_db()
    conn.execute(
        "INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs) VALUES (?, ?, ?, 0)",
        (USER, bot_db._current_week_start(), limit - 1),
    )
    conn.commit()
    conn.close()

    racers = 8
    barrier = threading.Barrier(racers)
    results: list[bool] = []
    lock = threading.Lock()

    def race() -> None:
        barrier.wait(timeout=10)
        won = bot_db.increment_usage(USER, scan=True)
        with lock:
            results.append(won)

    threads = [threading.Thread(target=race) for _ in range(racers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert sum(results) == 1, f"expected exactly one winner, got {sum(results)}"
    assert _used()[0] == limit, "quota was overdrawn by a concurrent request"


def test_bonus_balance_is_the_second_allowance_and_cannot_go_negative(bot_db) -> None:
    limit = config.TIERS["free"]["scans_per_week"]
    conn = get_db()
    conn.execute(
        "INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs) VALUES (?, ?, ?, 0)",
        (USER, bot_db._current_week_start(), limit),
    )
    conn.commit()
    conn.close()
    _set_bonus_scans(2)

    assert [bot_db.increment_usage(USER, scan=True) for _ in range(3)] == [True, True, False]
    conn = get_db()
    assert (
        conn.execute("SELECT bonus_scans FROM users WHERE user_id = ?", (USER,)).fetchone()[0] == 0
    )
    conn.close()
    assert _used()[0] == limit


def test_ai_messages_have_their_own_counter(bot_db) -> None:
    limit = config.TIERS["free"]["ai_msgs_per_week"]
    assert bot_db.increment_usage(USER, ai_msg=True) is True
    assert _used() == (0, 1)
    assert limit > 0


def test_expired_subscription_downgrades_on_read_and_refuses_the_paid_surface(bot_db) -> None:
    _activate("pro", int(time.time()) - 60)
    assert bot_db.get_user_tier(USER) == "free"
    conn = get_db()
    row = conn.execute("SELECT tier, tier_expires FROM users WHERE user_id = ?", (USER,)).fetchone()
    conn.close()
    assert (row["tier"], row["tier_expires"]) == ("free", 0)
    from rugmunchbot.premium import gating

    assert gating.gate(USER, "deepscan_forensic")[0] is False


def test_a_paid_tier_with_no_recorded_expiry_fails_closed(bot_db) -> None:
    """NULL/0 expiry on a paid row is a half-committed activation, not a licence."""
    conn = get_db()
    conn.execute("UPDATE users SET tier = 'pro', tier_expires = NULL WHERE user_id = ?", (USER,))
    conn.commit()
    conn.close()
    assert bot_db.get_user_tier(USER) == "free"


def test_last_weeks_rows_do_not_count_toward_this_weeks_quota(bot_db) -> None:
    conn = get_db()
    conn.execute(
        "INSERT INTO weekly_usage (user_id, week_start, scans, ai_msgs) VALUES (?, '2000-01-03', 99, 99)",
        (USER,),
    )
    conn.commit()
    conn.close()
    assert _used() == (0, 0)
    assert bot_db.increment_usage(USER, scan=True) is True
    assert _used() == (1, 0)


def test_check_rate_limit_agrees_with_the_atomic_claim(bot_db) -> None:
    limit = config.TIERS["free"]["scans_per_week"]
    assert bot_db.check_rate_limit(USER, "scan")[0] is True
    for _ in range(limit):
        bot_db.increment_usage(USER, scan=True)
    assert bot_db.check_rate_limit(USER, "scan") == (False, limit, limit)
    assert bot_db.increment_usage(USER, scan=True) is False
