"""Continuation of test_updates_digest.py - split for the file-size ceiling."""

from __future__ import annotations

from typing import Any


def test_channel_updates_job_registered():
    class _JQ:
        def __init__(self):
            self.repeating: list[dict[str, Any]] = []
            self.daily: list[dict[str, Any]] = []

        def run_repeating(self, callback, interval, first=None, name=None, **kw):
            self.repeating.append({"name": name})

        def run_daily(self, callback, time, days=None, name=None, **kw):
            self.daily.append({"name": name})

        def run_once(self, callback, when, name=None, **kw):
            pass

    class _App:
        def __init__(self):
            self.job_queue = _JQ()

    from rugmunchbot.channels.scheduler import _register as reg

    app = _App()
    flags = {
        "CHANNEL_JOBS_ENABLED": True,
        "ALERTS_CHANNEL_ENABLED": False,
        "ALPHA_CHANNEL_ENABLED": False,
        "SCAN_CHANNEL_ENABLED": False,
        "TG_NEWS_ENABLED": False,
        "SCAM_WATCH_ENABLED": False,
        "PERSONAL_CHANNEL_ENABLED": False,
        "DAILY_MARKET_SUMMARY_ENABLED": False,
        "UPDATES_CHANNEL_ENABLED": True,
    }
    from _registrars import flag_owner

    owners = {k: flag_owner(k) for k in flags}
    originals = {k: getattr(owners[k], k, None) for k in flags}
    try:
        for k, v in flags.items():
            setattr(owners[k], k, v)
        reg.register_channel_jobs(app)
    finally:
        for k, v in originals.items():
            setattr(owners[k], k, v)
    assert "channel_updates" in [j["name"] for j in app.job_queue.daily]
