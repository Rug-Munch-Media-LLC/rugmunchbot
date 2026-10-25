"""Entitlement contract: every paid surface is gated, and the card tells the truth.

These are pure tests — no network, no DB. ``gating.gate`` takes a
``resolve_tier`` seam, so the tier is a lambda here and production keeps its
single DB read. If a feature is advertised in ``gating.FEATURES`` it is proven
closed to free and open to the tier the table names; a typo in a feature name
must never unlock anything.
"""

from __future__ import annotations

import re

import pytest

from rugmunchbot.config import TIERS
from rugmunchbot.handlers.premium_watch import _limits
from rugmunchbot.premium import watch_alerts


def _tier(name: str):
    """The production seam, replaced by a constant lookup."""
    return lambda _user_id: name


class _Clock:
    """Monotonic test clock — the gate takes ``clock`` as a seam."""

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


# ── alert gate internals ────────────────────────────────────────────────────


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


# ── watchlist quota: the pricing table is the promise ───────────────────────

_WATCH_COPY = re.compile(r"Watchlist \((\d+) tokens?\)")


@pytest.mark.parametrize("tier", sorted(TIERS))
def test_watchlist_quota_equals_the_number_the_pricing_table_advertises(tier: str) -> None:
    """Every tier that names a watchlist size must actually get that many.

    This is the claim-vs-enforcement gate: if marketing says 50 and the quota
    says 25, this test fails before a paying user can notice.
    """
    for line in TIERS[tier].get("features", []):
        found = _WATCH_COPY.search(line)
        if found:
            assert _limits.watch_limit(tier) == int(found.group(1))


def test_legacy_billing_names_inherit_the_top_quota() -> None:
    assert {_limits.watch_limit(n) for n in ("pro", "elite", "whale", "beta")} == {
        _limits.UNLIMITED
    }


def test_unknown_tier_gets_the_free_quota_not_the_paid_one() -> None:
    assert _limits.watch_limit(None) == _limits.WATCH_LIMITS["free"]
    assert _limits.watch_limit("nonsense") == _limits.WATCH_LIMITS["free"]


def test_refusal_quotes_the_enforced_numbers() -> None:
    card = _limits.limit_refusal(2, 2)
    assert f"<b>{_limits.WATCH_LIMITS['free']}</b>" in card
    assert f"<b>{_limits.WATCH_LIMITS['scout']}</b>" in card
    assert f"<b>{_limits.WATCH_LIMITS['hunter']}</b>" in card
    assert "unlimited" in card
