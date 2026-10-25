"""Entitlement contract: every paid surface is gated, and the card tells the truth.

These are pure tests — no network, no DB. ``gating.gate`` takes a
``resolve_tier`` seam, so the tier is a lambda here and production keeps its
single DB read. If a feature is advertised in ``gating.FEATURES`` it is proven
closed to free and open to the tier the table names; a typo in a feature name
must never unlock anything.
"""

from __future__ import annotations

import pytest

from rugmunchbot.premium import deepscan_job, gating, scam_feed


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


