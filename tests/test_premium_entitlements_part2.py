"""Entitlement contract: every paid surface is gated, and the card tells the truth.

These are pure tests — no network, no DB. ``gating.gate`` takes a
``resolve_tier`` seam, so the tier is a lambda here and production keeps its
single DB read. If a feature is advertised in ``gating.FEATURES`` it is proven
closed to free and open to the tier the table names; a typo in a feature name
must never unlock anything.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from rugmunchbot.config import TIERS
from rugmunchbot.premium import deepscan_job, gating, quota, watch_alerts


def _tier(name: str):
    """The production seam, replaced by a constant lookup."""
    return lambda _user_id: name


# ── the feature table itself ────────────────────────────────────────────────


def test_cancel_is_idempotent_and_only_the_first_call_wins() -> None:
    registry = deepscan_job.JobRegistry()
    job = registry.start(2, "addr", "solana", "free")
    assert registry.cancel(job.job_id) is True
    assert registry.cancel(job.job_id) is False
    assert job.status == deepscan_job.CANCELLED
    assert "No quota was consumed" in deepscan_job.render_progress(job)


# ── weekly meter ────────────────────────────────────────────────────────────


def test_next_reset_is_the_upcoming_monday_and_never_now() -> None:
    monday = datetime(2026, 10, 19, tzinfo=UTC)  # a Monday, exactly 00:00
    assert quota.next_reset(monday) == datetime(2026, 10, 26, tzinfo=UTC)
    sunday = datetime(2026, 10, 25, 23, 59, tzinfo=UTC)
    assert quota.next_reset(sunday) == datetime(2026, 10, 26, tzinfo=UTC)
    midweek = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    assert quota.next_reset(midweek) == datetime(2026, 10, 26, tzinfo=UTC)


def test_meter_limits_come_from_the_tier_not_from_a_second_table() -> None:
    usage = quota.snapshot("scout", {"scans": 0, "ai_msgs": 0})
    assert usage.limits["scans"] == TIERS["scout"]["scans_per_week"]
    assert usage.limits["ai_msgs"] == TIERS["scout"]["ai_msgs_per_week"]


def test_remaining_is_floored_at_zero_even_when_overdrawn() -> None:
    usage = quota.snapshot("free", {"scans": 10_000, "ai_msgs": 0})
    assert usage.remaining("scans") == 0


def test_card_shows_the_reset_and_the_next_rung() -> None:
    card = quota.render(quota.snapshot("free", {"scans": 1, "ai_msgs": 0}))
    assert "Resets in" in card
    assert TIERS["scout"]["name"] in card
    assert quota.next_tier("pro") is None
    assert quota.next_tier("free") == "scout"


# ── alert gate: advertised cap == enforced cap ──────────────────────────────


@pytest.mark.parametrize(
    ("tier", "cap"),
    [("free", 5), (None, 5), ("nonsense", 5), ("scout", 20), ("hunter", 20), ("pro", 20)],
)
def test_hourly_cap_is_the_number_the_card_advertises(tier: str | None, cap: int) -> None:
    assert watch_alerts.cap_for(tier) == cap
    assert watch_alerts.AlertGate(tier=tier).cap == cap


def test_gate_defaults_to_the_free_cap_when_no_tier_is_given() -> None:
    """Fail closed: an unconfigured gate must not hand out the paid allowance."""
    assert watch_alerts.AlertGate().cap == watch_alerts.HOURLY_CAP["free"]


class _Clock:
    def __init__(self, at: float = 0.0) -> None:
        self.at = at

    def __call__(self) -> float:
        return self.at


def _open_gate(tier: str = "pro", clock=None) -> watch_alerts.AlertGate:
    return watch_alerts.AlertGate(
        quiet=watch_alerts.QuietHours(start=0, end=0),  # window disabled
        tier=tier,
        clock=clock or _Clock(),
    )


def test_dedupe_drops_a_repeat_then_allows_the_key_again_after_the_window() -> None:
    clock = _Clock()
    gate = _open_gate(clock=clock)
    assert gate.admit(1, "token-a:lp") == "send"
    assert gate.admit(1, "token-a:lp") == "drop"
    clock.at += watch_alerts.WINDOW_S + 1
    assert gate.admit(1, "token-a:lp") == "send"


def test_rate_limit_drops_the_next_distinct_alert_inside_the_hour() -> None:
    gate = _open_gate(tier="free")  # 5/hour
    outcomes = [gate.admit(1, f"token-{i}") for i in range(7)]
    assert outcomes == ["send"] * 5 + ["drop"] * 2


def test_quiet_hours_hold_without_counting_a_send_and_release_later() -> None:
    gate = watch_alerts.AlertGate(
        quiet=watch_alerts.QuietHours(start=0, end=24), tier="free", clock=_Clock()
    )
    assert gate.admit(1, "token-q") == "hold"
    assert gate.admit(1, "token-q") == "hold"
    assert gate.held_count(1) == 1
    assert gate.release(1) == ["token-q"]
    assert gate.held_count(1) == 0


