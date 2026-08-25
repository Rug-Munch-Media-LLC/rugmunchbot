"""WO-2 channel router matrix + main-channel hard cap (2026-08-25).

Covers /root/.knowledge/CONTENT-STRATEGY-CANONICAL.md:

* ``route_item`` — pure severity/type → channel matrix (alerts severity-
  gated FIRST, main gate additive, native lanes per type).
* ``demote_main`` — overflow destination when the main cap blocks.
* ``MainChannelCap`` — HARD ≤9 posts / 24h sliding window; Redis-durable
  with in-process mirror; failed sends never consume budget (record only
  after send ok); old entries prune out of the window.
"""

from __future__ import annotations

import time

import pytest

from rugmunchbot.channels.main_cap import CAP_WINDOW_SECONDS, MainChannelCap
from rugmunchbot.channels.router import demote_main, route_item, route_news

# ── route_item matrix ────────────────────────────────────────────────────


def test_alert_critical_routes_to_alerts() -> None:
    assert route_item("alert", "critical", 0.9) == "alerts"


def test_alert_high_routes_to_alerts_even_below_news_floor() -> None:
    # Severity gate beats importance: a HIGH alert is alert-channel material.
    assert route_item("alert", "high", 0.2) == "alerts"


def test_alert_medium_is_skipped_no_matter_what() -> None:
    # Matrix law: alerts channel has NO filler — non-CRITICAL/HIGH drops.
    assert route_item("alert", "medium", 0.95) == "skip"
    assert route_item("alert", "low", 0.5) == "skip"


def test_main_gate_wins_for_non_alert_types() -> None:
    assert route_item("news", "medium", 0.8) == "main"
    assert route_item("scan", "", 0.7) == "main"
    assert route_item("shill", "", 0.9) == "main"
    assert route_item("whale", "", 0.75) == "main"


def test_native_lanes_below_main_gate() -> None:
    assert route_item("scan", "", 0.5) == "scans"
    assert route_item("shill", "", 0.3) == "shills"
    assert route_item("whale", "", 0.4) == "alpha"
    assert route_item("alpha", "", 0.4) == "alpha"


def test_news_type_falls_through_importance_bands() -> None:
    assert route_item("news", "", 0.5) == "news"
    assert route_item("news", "", 0.2) == "skip"


def test_unknown_type_treated_as_news() -> None:
    assert route_item("", "", 0.5) == "news"
    assert route_item("weird", "", 0.2) == "skip"


def test_route_news_still_works() -> None:
    # Legacy band router unchanged.
    assert route_news(0.8, "news") == "main"
    assert route_news(0.5, "news") == "news"
    assert route_news(0.5, "alpha") == "alpha"
    assert route_news(0.1, "news") == "skip"


# ── demote_main ──────────────────────────────────────────────────────────


def test_demote_maps_to_native_lanes() -> None:
    assert demote_main("news") == "news"
    assert demote_main("shill") == "shills"
    assert demote_main("scan") == "scans"
    assert demote_main("alert") == "alerts"
    assert demote_main("whale") == "alpha"


def test_demote_unknown_defaults_to_news() -> None:
    assert demote_main("") == "news"
    assert demote_main("??") == "news"


# ── MainChannelCap ───────────────────────────────────────────────────────


@pytest.fixture
def local_cap(monkeypatch):
    """Cap with Redis unavailable → in-process enforcement only."""
    monkeypatch.setattr("rugmunchbot.channels.main_cap.get_redis_async", lambda: None)
    return MainChannelCap(cap=3)


@pytest.mark.asyncio
async def test_cap_admits_under_limit(local_cap) -> None:
    for _ in range(3):
        allowed, n = await local_cap.admission()
        assert allowed
        await local_cap.record()
    assert n == 2 or n == 3


@pytest.mark.asyncio
async def test_cap_denies_over_limit(local_cap) -> None:
    for _ in range(3):
        await local_cap.record()
    allowed, n = await local_cap.admission()
    assert not allowed
    assert n == 3


@pytest.mark.asyncio
async def test_cap_window_slides(local_cap) -> None:
    for _ in range(3):
        await local_cap.record()
    # Age every entry past the window → room again.
    aged = time.time() - CAP_WINDOW_SECONDS - 10
    local_cap._local.clear()
    local_cap._local.extend([aged] * 3)
    allowed, n = await local_cap.admission()
    assert allowed
    assert n == 0


@pytest.mark.asyncio
async def test_cap_redis_down_falls_back_to_local(local_cap) -> None:
    # get_redis_async returns None → count/record must not raise and must
    # still enforce via the in-process mirror.
    await local_cap.record()
    await local_cap.record()
    allowed, n = await local_cap.admission()
    assert allowed
    assert n == 2


@pytest.mark.asyncio
async def test_cap_never_raises_on_redis_errors(monkeypatch) -> None:
    class _Boom:
        def zremrangebyscore(self, *a, **k):
            raise ConnectionError("redis gone")

        async def zcard(self, *a, **k):
            raise ConnectionError("redis gone")

        async def zadd(self, *a, **k):
            raise ConnectionError("redis gone")

        async def expire(self, *a, **k):
            raise ConnectionError("redis gone")

    cap = MainChannelCap(cap=2)
    monkeypatch.setattr("rugmunchbot.channels.main_cap.get_redis_async", lambda: _Boom())
    cap._redis_down = False
    cap._redis = None
    await cap.record()  # redis add fails → local mirror still records
    allowed, n = await cap.admission()  # redis read fails → local count
    assert allowed
    assert n == 1
