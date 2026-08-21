"""Bot scheduler wiring — exactly 4 CORE repeating jobs (audit 2026-08-21).

``main()`` ends in the blocking ``run_polling`` and needs a live Telegram
token, so the scheduling contract is unit-tested against the extracted
:func:`rugmunchbot.bot.register_core_jobs` with a FAKE
``Application.builder`` chain + fake ``job_queue`` (no network, no Redis).

Contract under test:
  * EXACTLY 4 core repeating jobs: check_alerts, check_monitors,
    deliver_pending_broadcasts, deliver_pending_alerts.
  * Intervals/first-runs match the production wiring.
  * The daily scam-tip job stays DISABLED (no ``run_daily``).
  * Channel content jobs are NOT core — they are env-driven and owned by
    ``register_channel_jobs`` (asserted absent here so a future edit that
    accidentally double-registers them into the core lane is caught).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot import bot as bot_mod


class FakeJobQueue:
    """Records run_repeating / run_daily / run_once registrations."""

    def __init__(self) -> None:
        self.repeating: list[dict[str, Any]] = []
        self.daily: list[dict[str, Any]] = []
        self.once: list[dict[str, Any]] = []

    def run_repeating(self, callback, interval, first=None, last=None, name=None, **kw):
        self.repeating.append(
            {
                "callback": callback,
                "name": name or getattr(callback, "__name__", "?"),
                "interval": interval,
                "first": first,
            }
        )

    def run_daily(self, callback, time, name=None, **kw):
        self.daily.append({"callback": callback, "name": name})

    def run_once(self, callback, when, name=None, **kw):
        self.once.append({"callback": callback, "name": name})


class FakeApplication:
    def __init__(self) -> None:
        self.job_queue = FakeJobQueue()


class FakeBuilder:
    """Stands in for ``telegram.ext.Application.builder()`` — no network."""

    def __init__(self, app: FakeApplication) -> None:
        self._app = app

    def token(self, _t):
        return self

    def request(self, _r):
        return self

    def concurrent_updates(self, _n):
        return self

    def build(self):
        return self._app


def _build_fake_app() -> FakeApplication:
    """Drive the fake builder chain exactly like ``main()`` does."""
    app = FakeApplication()
    return FakeBuilder(app).token("fake-token").request(None).concurrent_updates(4).build()


def test_core_scheduler_registers_exactly_four_repeating_jobs():
    app = _build_fake_app()
    bot_mod.register_core_jobs(app)
    jobs = app.job_queue.repeating
    assert len(jobs) == 4
    assert [j["name"] for j in jobs] == [
        "check_alerts",
        "check_monitors",
        "deliver_pending_broadcasts",
        "deliver_pending_alerts",
    ]


def test_core_scheduler_intervals_match_production():
    app = _build_fake_app()
    bot_mod.register_core_jobs(app)
    by_name = {j["name"]: j for j in app.job_queue.repeating}
    assert by_name["check_alerts"]["interval"] == bot_mod.ALERT_CHECK_INTERVAL
    assert by_name["check_alerts"]["first"] == 10
    assert by_name["check_monitors"]["interval"] == bot_mod.MONITOR_CHECK_INTERVAL
    assert by_name["check_monitors"]["first"] == 120
    assert by_name["deliver_pending_broadcasts"]["interval"] == 30
    assert by_name["deliver_pending_broadcasts"]["first"] == 15
    assert by_name["deliver_pending_alerts"]["interval"] == 30
    assert by_name["deliver_pending_alerts"]["first"] == 15


def test_core_scheduler_has_no_daily_or_channel_jobs():
    app = _build_fake_app()
    bot_mod.register_core_jobs(app)
    # daily_scam_tip disabled 2026-08-12 — must not silently return.
    assert app.job_queue.daily == []
    # Channel jobs belong to register_channel_jobs, never the core lane.
    assert app.job_queue.once == []
    names = {j["name"] for j in app.job_queue.repeating}
    assert not any(n.startswith("channel_") for n in names)


def test_core_scheduler_no_job_queue_is_a_noop():
    app = _build_fake_app()
    app.job_queue = None
    # Must not raise when PTB provides no queue (matches old inline guard).
    bot_mod.register_core_jobs(app)
