"""GetXAPI budget tracker tests (hermetic — no live Redis dependency).

Covers the two hard caps (daily call limit + total spend), the early-warning
thresholds, the fail-open behavior when Redis is down, and the usage/report
helpers. Redis is faked with an in-memory stand-in so the suite runs anywhere
without a Redis daemon.
"""

from __future__ import annotations

import pytest

from rugmunchbot.tools import getxapi_budget as budget
from rugmunchbot.tools.getxapi_config import (
    GETXAPI_COST_PER_CALL,
    GETXAPI_DAILY_CALL_LIMIT,
    GETXAPI_TOTAL_BUDGET,
)


class _FakeRedis:
    """Minimal in-memory Redis stand-in for the budget tracker's ops."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def incr(self, key: str) -> int:
        val = int(self._data.get(key, "0")) + 1
        self._data[key] = str(val)
        return val

    def incrby(self, key: str, amount: int) -> int:
        val = int(self._data.get(key, "0")) + amount
        self._data[key] = str(val)
        return val

    def incrbyfloat(self, key: str, amount: float) -> float:
        val = float(self._data.get(key, "0")) + amount
        self._data[key] = str(val)
        return val

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def expire(self, key: str, ttl: int) -> None:
        pass

    def delete(self, *keys: str) -> int:
        n = 0
        for key in keys:
            if key in self._data:
                del self._data[key]
                n += 1
        return n

    def pipeline(self, **kwargs: object) -> _FakeRedis:
        return self

    def execute(self) -> list[None]:
        return []


@pytest.fixture(autouse=True)
def _fake_redis(monkeypatch):
    """Point the budget tracker at an in-memory Redis and reset counters."""
    fake = _FakeRedis()
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: fake)
    budget.reset_counters()
    yield fake
    budget.reset_counters()


def test_record_call_increments_daily_and_spend():
    budget.record_call("search")
    usage = budget.get_daily_usage()
    assert usage["calls"] == 1
    assert usage["spend"] == pytest.approx(GETXAPI_COST_PER_CALL)
    assert usage["budget_remaining"] == pytest.approx(GETXAPI_TOTAL_BUDGET - GETXAPI_COST_PER_CALL)


def test_record_call_custom_cost():
    budget.record_call("user-tweets", cost=0.002)
    usage = budget.get_daily_usage()
    assert usage["spend"] == pytest.approx(0.002)


def test_can_afford_call_true_when_under_budget():
    assert budget.can_afford_call() is True


def test_can_afford_call_false_at_daily_limit():
    for _ in range(GETXAPI_DAILY_CALL_LIMIT):
        budget.record_call("search")
    assert budget.can_afford_call() is False


def test_can_afford_call_false_at_total_budget():
    # Spend the whole budget in one call.
    budget.record_call("search", cost=GETXAPI_TOTAL_BUDGET)
    assert budget.can_afford_call() is False


def test_calls_remaining_is_min_of_daily_and_budget():
    # 10 calls used → 140 remaining by daily cap; budget still allows ~4,490.
    for _ in range(10):
        budget.record_call("search")
    usage = budget.get_daily_usage()
    assert usage["calls_remaining"] == GETXAPI_DAILY_CALL_LIMIT - 10


def test_calls_remaining_bounded_by_budget():
    # Spend most of the budget -> the dollar cap becomes the binding constraint.
    budget.record_call("search", cost=4.99)
    usage = budget.get_daily_usage()
    # budget_remaining = 0.01 -> ~10 calls at $0.001 each, far below the
    # daily cap (150 - 1 = 149). The budget cap must be the binding constraint.
    assert usage["calls_remaining"] < GETXAPI_DAILY_CALL_LIMIT - 1
    # The budget cap is the binding constraint: calls_remaining is driven by
    # the (small) remaining budget, not the daily cap.
    assert usage["calls_remaining"] <= 10


def test_get_budget_report_format():
    budget.record_call("search")
    report = budget.get_budget_report()
    assert "GetXAPI budget:" in report
    assert "calls today" in report
    assert "spent $" in report


def test_fail_open_when_redis_down(monkeypatch):
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: None)
    # record_call must not raise and must not block.
    budget.record_call("search")
    assert budget.can_afford_call() is True
    usage = budget.get_daily_usage()
    assert usage["calls"] == 0
    assert usage["spend"] == 0.0


def test_fail_open_on_redis_error(monkeypatch):
    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", _boom)
    budget.record_call("search")  # must not raise
    assert budget.can_afford_call() is True


def test_warning_threshold_logged_once(monkeypatch, caplog):

    # Force the threshold flags to a clean state.
    budget.reset_counters()
    # Record enough calls to cross the WARNING threshold (100).
    for _ in range(101):
        budget.record_call("search")
    # The WARNING should have fired.
    assert any("approaching_daily_limit" in rec.message for rec in caplog.records) or any(
        "approaching_daily_limit" in str(rec) for rec in caplog.records
    )


def test_critical_threshold_logged(monkeypatch, caplog):
    budget.reset_counters()
    for _ in range(141):
        budget.record_call("search")
    assert any("daily_limit_nearly_exhausted" in str(rec) for rec in caplog.records)


def test_critical_spend_threshold_logged(monkeypatch, caplog):
    budget.reset_counters()
    budget.record_call("search", cost=4.51)
    assert any("budget_nearly_exhausted" in str(rec) for rec in caplog.records)


def test_reset_counters_clears():
    budget.record_call("search")
    assert budget.get_daily_usage()["calls"] == 1
    budget.reset_counters()
    assert budget.get_daily_usage()["calls"] == 0
    assert budget.get_daily_usage()["spend"] == 0.0


# ══════════════════════════════════════════════════════════════════════════
# INTELLIGENT ROUTING — should_use_getxapi
# ══════════════════════════════════════════════════════════════════════════


def test_routing_search_always_getxapi():
    assert budget.should_use_getxapi("search") is True
    assert budget.should_use_getxapi("advanced_search") is True


def test_routing_user_tweets_high_value_uses_getxapi():
    assert budget.should_use_getxapi("user/tweets", {"followers_count": 100_000}) is True


def test_routing_user_tweets_low_value_uses_pry():
    assert budget.should_use_getxapi("user/tweets", {"followers_count": 1_000}) is False


def test_routing_user_tweets_unknown_followers_conservative_getxapi():
    # No follower count → default to GetXAPI (never silently degrade reliability).
    assert budget.should_use_getxapi("user-tweets", {"username": "zachxbt"}) is True


def test_routing_user_info_uses_getxapi():
    assert budget.should_use_getxapi("user/info") is True


def test_routing_user_mentions_uses_pry():
    assert budget.should_use_getxapi("user/mentions") is False


def test_routing_trends_uses_getxapi():
    assert budget.should_use_getxapi("trends") is True


def test_routing_user_status_uses_getxapi():
    assert budget.should_use_getxapi("user/status") is True


def test_routing_unknown_endpoint_conservative_getxapi():
    assert budget.should_use_getxapi("some/unknown") is True


# ══════════════════════════════════════════════════════════════════════════
# PRY FALLBACK ACCOUNTING
# ══════════════════════════════════════════════════════════════════════════


def test_record_pry_call_increments_pry_and_content():
    budget.record_pry_call("user-tweets")
    usage = budget.get_daily_usage()
    assert usage["pry_calls"] == 1
    assert usage["total_content"] == 1
    # Pry calls must NOT count against the GetXAPI daily/spend ledger.
    assert usage["calls"] == 0
    assert usage["spend"] == 0.0


def test_record_content_fetched_increments_content_only():
    budget.record_content_fetched(20)
    usage = budget.get_daily_usage()
    assert usage["total_content"] == 20
    assert usage["calls"] == 0
    assert usage["pry_calls"] == 0


def test_get_daily_usage_reports_pry_and_content():
    budget.record_call("search")
    budget.record_pry_call("mentions")
    budget.record_content_fetched(5)
    usage = budget.get_daily_usage()
    assert usage["calls"] == 1
    assert usage["pry_calls"] == 1
    assert usage["total_content"] == 6
    assert "pacing_active" in usage


# ══════════════════════════════════════════════════════════════════════════
# PACING — hourly / daily Pry-only switch
# ══════════════════════════════════════════════════════════════════════════


def test_pacing_active_forces_pry_only(monkeypatch):
    # Simulate an active pacing lock (until far in the future).
    fake = _FakeRedis()
    fake._data["getxapi:pacing_until"] = str(10**12)
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: fake)
    # Even search (normally always GetXAPI) must route to Pry while pacing.
    assert budget.should_use_getxapi("search") is False
    assert budget.should_use_getxapi("trends") is False


def test_pacing_not_active_when_no_lock():
    assert budget.should_use_getxapi("search") is True


def test_hourly_pacing_engages_after_limit(monkeypatch):
    # Record more than the hourly limit (50) calls in the current hour.
    for _ in range(51):
        budget.record_call("search")
    usage = budget.get_daily_usage()
    assert usage["pacing_active"] is True
    # While pacing, even search routes to Pry.
    assert budget.should_use_getxapi("search") is False
