"""Scheduler job-registration contract: which channel owns which tick.

Split out of test_whale_alpha_routing.py so each module stays small; the
registration seam lives here with the tests that exercise it.
"""

from __future__ import annotations

import inspect
from typing import Any


class _JQ:
    def __init__(self):
        self.repeating: list[dict[str, Any]] = []
        self.once: list[dict[str, Any]] = []

    def run_repeating(self, callback, interval, **kw):
        self.repeating.append({"callback": callback, "name": kw.get("name"), "interval": interval})

    def run_once(self, callback, when, name=None, **kw):
        self.once.append({"name": name})

    def run_daily(self, callback, time, **kw):
        pass


class _App:
    def __init__(self):
        self.job_queue = _JQ()


_CHANNEL_FLAGS = (
    "CHANNEL_JOBS_ENABLED",
    "ALERTS_CHANNEL_ENABLED",
    "ALPHA_CHANNEL_ENABLED",
    "SCAN_CHANNEL_ENABLED",
    "TG_NEWS_ENABLED",
    "SCAM_WATCH_ENABLED",
    "PERSONAL_CHANNEL_ENABLED",
    "DAILY_MARKET_SUMMARY_ENABLED",
    "UPDATES_CHANNEL_ENABLED",
)

_OPT_IN_ENV = (
    "RMI_RAG_POSTS_ENABLED",
    "RMI_DAILY_QUALITY_ENABLED",
    "RMI_SHILL_WATCH_ENABLED",
)


def _configure_channels(monkeypatch, *, enabled=(), chat_ids=None, opt_in=()):
    """Apply the scheduler channel switches through one seam."""
    from rugmunchbot.channels.scheduler import _register as reg

    flags = dict.fromkeys(_CHANNEL_FLAGS, False)
    flags["CHANNEL_JOBS_ENABLED"] = True
    flags.update(dict.fromkeys(enabled, True))
    monkeypatch.setattr(reg, "_chat_id", (chat_ids or {}).get)
    for flag, value in flags.items():
        monkeypatch.setattr(reg, flag, value)
    for name in _OPT_IN_ENV:
        monkeypatch.delenv(name, raising=False)
    for name in opt_in:
        monkeypatch.setenv(name, "true")


def test_whale_job_registers_when_alpha_enabled(monkeypatch):
    """The whale tick lives on the ALPHA channel now, not alerts."""
    from rugmunchbot.channels.scheduler import _register as reg

    _configure_channels(
        monkeypatch,
        enabled=("ALERTS_CHANNEL_ENABLED", "ALPHA_CHANNEL_ENABLED"),
        chat_ids={"alpha": "-100alpha"},
    )

    app = _App()
    reg.register_channel_jobs(app)
    names = [j["name"] for j in app.job_queue.repeating]
    assert "channel_whale_alerts" in names


def test_shill_watch_job_registers_on_alerts(monkeypatch):
    from rugmunchbot.channels.scheduler import _register as reg

    _configure_channels(
        monkeypatch,
        enabled=("ALERTS_CHANNEL_ENABLED",),
        chat_ids={"alerts": "-100alerts"},
        opt_in=("RMI_SHILL_WATCH_ENABLED",),
    )

    app = _App()
    reg.register_channel_jobs(app)
    names = [j["name"] for j in app.job_queue.repeating]
    assert "channel_shill_watch" in names


def test_daily_quality_job_registers_on_main(monkeypatch):
    from rugmunchbot.channels.scheduler import _register as reg

    _configure_channels(
        monkeypatch,
        chat_ids={"main": "-100main"},
        opt_in=("RMI_DAILY_QUALITY_ENABLED",),
    )

    app = _App()
    reg.register_channel_jobs(app)
    names = [j["name"] for j in app.job_queue.repeating]
    assert "channel_daily_quality" in names


def test_dislocation_and_social_spikes_route_to_alpha(monkeypatch):
    """2026-08-29 doctrine: funding dislocation + social spikes are ALPHA
    content (CONTENT-STRATEGY-CANONICAL), never @rmialerts."""
    from rugmunchbot.channels.scheduler import _register as reg

    _configure_channels(
        monkeypatch,
        enabled=("ALERTS_CHANNEL_ENABLED", "ALPHA_CHANNEL_ENABLED"),
        chat_ids={"alpha": "-100alpha", "alerts": "-100alerts"},
    )

    app = _App()
    reg.register_channel_jobs(app)
    names = {j["name"] for j in app.job_queue.repeating}

    dislo = next((j for j in app.job_queue.repeating if j["name"] == "channel_dislocation"), None)
    assert dislo is not None, "dislocation job must register when alpha enabled"

    source = inspect.getsource(dislo["callback"])
    assert '_chat_id("alpha")' in source
    assert '_chat_id("alerts")' not in source

    social = next(
        (j for j in app.job_queue.repeating if j["name"] == "channel_social_spikes"), None
    )
    assert social is not None, "social spikes job must register when alpha enabled"
    source = inspect.getsource(social["callback"])
    assert '_chat_id("alpha")' in source
    assert '_chat_id("alerts")' not in source

    # The alerts channel digest job still registers (scanner risk digest).
    assert "channel_alerts" in names
