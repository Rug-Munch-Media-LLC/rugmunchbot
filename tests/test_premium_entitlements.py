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
from rugmunchbot.premium import deepscan_job, gating, quota, scam_feed, watch_alerts


def _tier(name: str):
    """The production seam, replaced by a constant lookup."""
    return lambda _user_id: name


# ── the feature table itself ────────────────────────────────────────────────


@pytest.mark.parametrize("feature", sorted(gating.FEATURES))
def test_free_is_refused_every_advertised_feature(feature: str) -> None:
    """No entry in the paid table may be reachable from the free tier."""
    assert gating.allows("free", feature) is False


@pytest.mark.parametrize("feature", sorted(gating.FEATURES))
def test_the_tier_the_table_names_is_allowed(feature: str) -> None:
    """The rank in FEATURES is the rank that opens it — read back, not guessed."""
    needed = gating.FEATURES[feature]
    opener = next(name for name, rank in gating.RANK.items() if rank == needed)
    assert gating.allows(opener, feature) is True


def test_unknown_feature_is_refused_for_the_top_tier() -> None:
    """A typo must not unlock a paid surface."""
    assert gating.allows("pro", "deepscan_forensik") is False


def test_unknown_and_missing_tiers_are_free() -> None:
    for tier in (None, "", "  ", "nonsense", "PRO  "):
        assert gating.rank_of(tier) == (3 if tier == "PRO  " else 0)


def test_legacy_billing_names_share_the_top_rank() -> None:
    """elite/whale/beta are old billing names; they must not rank differently."""
    assert {gating.rank_of(n) for n in ("pro", "elite", "whale", "beta")} == {3}


def test_kol_tracker_is_a_hunter_feature_end_to_end() -> None:
    allowed, why = gating.gate(7, "kol_tracker", resolve_tier=_tier("scout"))
    assert allowed is False
    assert "Hunter" in why
    assert "Scout" in why
    assert gating.gate(7, "kol_tracker", resolve_tier=_tier("hunter"))[0] is True


def test_refusal_names_the_command_that_lifts_it() -> None:
    _, why = gating.gate(1, "deepscan_forensic", resolve_tier=_tier("free"))
    assert "/premium" in why
    assert "/pricing" in why


def test_scam_feed_uses_the_same_gate() -> None:
    assert scam_feed.subscriber_only(1, resolve_tier=_tier("free"))[0] is False
    assert scam_feed.subscriber_only(1, resolve_tier=_tier("scout"))[0] is True


# ── forensic modules ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("tier", "want"),
    [
        ("free", ("CORE",)),
        ("scout", ("CORE", "DEEP")),
        ("hunter", ("CORE", "DEEP", "PREMIUM")),
        ("pro", ("CORE", "DEEP", "PREMIUM", "INTEL", "SENTINEL")),
    ],
)
def test_deepscan_module_tiers_follow_the_rank(tier: str, want: tuple[str, ...]) -> None:
    assert deepscan_job.allowed_modules(tier) == want
    assert set(deepscan_job.blocked_modules(tier)).isdisjoint(want)


def test_a_second_scan_supersedes_the_first_and_the_first_ignores_late_steps() -> None:
    registry = deepscan_job.JobRegistry()
    first = registry.start(1, "addr-a", "solana", "pro")
    second = registry.start(1, "addr-b", "solana", "pro")
    assert first.cancelled is True
    assert second.status == deepscan_job.RUNNING
    assert registry.advance(first.job_id, "verdict") is first
    assert first.stage != "verdict"  # a cancelled job never advances
    registry.finish(second.job_id)
    assert registry.active(1) is None


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


def test_dedupe_memory_is_bounded_by_the_window() -> None:
    clock = _Clock()
    gate = _open_gate(clock=clock)
    for i in range(50):
        gate.admit(1, f"token-{i}")
    clock.at += watch_alerts.WINDOW_S + 1
    gate.admit(1, "token-new")
    assert len(gate._state(1).seen) == 1


def test_free_alert_card_states_the_enforced_number() -> None:
    card = watch_alerts.render_alert({"symbol": "WIF", "score": 90}, tier="free")
    assert f"{watch_alerts.HOURLY_CAP['free']} alerts/hour" in card
