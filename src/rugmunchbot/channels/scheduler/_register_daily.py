"""Daily-report channel jobs — one registration step, split from ``_register``.

Every job here is a ``run_daily`` on the main channel, so a whole cadence reads
in one place and the structure gate stays shrink-only. The three flagship
editions (Morning Rug / Midday Report / Evening Wrap) are each a different
facet; the retired Midday Update stays behind its opt-in knob instead of being
deleted, so a cadence can be turned back on without a code change.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.daily_market_summary import (
    DAILY_MARKET_SUMMARY_ENABLED,
    post_daily_market_summary,
    summary_run_time,
)
from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._tick import _tick_wrapper
from rugmunchbot.config import UPDATES_CHANNEL_ENABLED, UPDATES_POST_HOUR_UTC


def _register_morning_rug(job_queue: Any) -> None:
    """The Morning Rug (07:00 America/New_York, 7/7) plus its midday variant.

    The midday edition is DISABLED by default (2026-10-17): it fires 30 minutes
    after the Midday Market Report and repeats its stories outside the
    three-edition dedupe.
    """
    if not (DAILY_MARKET_SUMMARY_ENABLED and _core._chat_id("main")):
        return
    job_queue.run_daily(
        _tick_wrapper("channel_morning_rug", post_daily_market_summary, envelope=True),
        time=summary_run_time(),
        days=tuple(range(7)),
        name="channel_morning_rug",
    )
    # NOTE: _dtime alias — a function-local ``from datetime import time as
    # _time`` elsewhere in this module would leave ``_time`` unbound here.
    from datetime import time as _dtime

    raw = os.getenv("RMI_DAILY_MARKET_BRIEF_MIDDAY_TIME", "13:00")
    try:
        hour, minute = (int(part) for part in raw.split(":", 1))
    except ValueError:
        hour, minute = 13, 0
    if os.getenv("RMI_MORNING_RUG_MIDDAY_ENABLED", "false").lower() in ("1", "true", "yes"):
        job_queue.run_daily(
            _tick_wrapper("channel_morning_rug_midday", post_daily_market_summary, envelope=True),
            time=_dtime(hour=hour, minute=minute, tzinfo=summary_run_time().tzinfo),
            days=tuple(range(7)),
            name="channel_morning_rug_midday",
        )


def _register_daily_rug(job_queue: Any) -> None:
    """The Daily Rug — flagship once-daily longform brief (date-guarded)."""
    from rugmunchbot.channels.daily_rug import (
        DAILY_RUG_ENABLED,
        daily_rug_run_time,
        post_daily_rug,
    )

    if DAILY_RUG_ENABLED and _core._chat_id("main"):
        job_queue.run_daily(
            _tick_wrapper("channel_daily_rug", post_daily_rug, envelope=True),
            time=daily_rug_run_time(),
            days=tuple(range(7)),
            name="channel_daily_rug",
        )


def _register_midday_update(job_queue: Any) -> None:
    """Retired Midday Update — opt-in only (``RMI_DAILY_RUG_UPDATE_ENABLED``).

    Disabled by default (2026-10-16): one flagship daily post, and the Midday
    Update duplicated the Morning Rug's stories.
    """
    from rugmunchbot.channels.daily_rug_update import (
        DAILY_RUG_UPDATE_ENABLED,
        post_daily_rug_update,
        rug_update_run_time,
    )

    if DAILY_RUG_UPDATE_ENABLED and _core._chat_id("main"):
        job_queue.run_daily(
            _tick_wrapper("channel_midday_update", post_daily_rug_update, envelope=True),
            time=rug_update_run_time(),
            days=tuple(range(7)),
            name="channel_midday_update",
        )


def _register_report_pair(job_queue: Any) -> None:
    """Midday Market Report (macro) + Evening Wrap (scams/shills/rugs).

    Three-way dedup with the Daily Rug lives in ``channels/_edition_dedup.py``;
    each edition is idempotent through its own date guard.
    """
    from rugmunchbot.channels.evening_wrap import (
        EVENING_WRAP_ENABLED,
        evening_wrap_run_time,
        post_evening_wrap,
    )
    from rugmunchbot.channels.midday_market_report import (
        MIDDAY_REPORT_ENABLED,
        midday_report_run_time,
        post_midday_report,
    )

    if _core._chat_id("main"):
        if MIDDAY_REPORT_ENABLED:
            job_queue.run_daily(
                _tick_wrapper("channel_midday_report", post_midday_report, envelope=True),
                time=midday_report_run_time(),
                days=tuple(range(7)),
                name="channel_midday_report",
            )
        if EVENING_WRAP_ENABLED:
            job_queue.run_daily(
                _tick_wrapper("channel_evening_wrap", post_evening_wrap, envelope=True),
                time=evening_wrap_run_time(),
                days=tuple(range(7)),
                name="channel_evening_wrap",
            )


def _register_updates(job_queue: Any) -> None:
    """@rmiupdates changelog — daily, deduped per calendar date by the poster."""
    from datetime import time as _time

    from rugmunchbot.channels.scheduler._posts_updates import post_updates_digest

    # Wired and wrapped, but only registered when the flag is on — record the
    # skip so the channel is never silently absent from scheduler telemetry.
    if UPDATES_CHANNEL_ENABLED:
        job_queue.run_daily(
            _tick_wrapper("channel_updates", post_updates_digest, envelope=True),
            time=_time(hour=UPDATES_POST_HOUR_UTC, minute=0),
            days=tuple(range(7)),
            name="channel_updates",
        )
    else:
        _core.logger.info("channels:disabled", job="channel_updates", reason="flag_off")


def register_daily_jobs(job_queue: Any) -> None:
    """Register the once-a-day main-channel editions and the changelog."""
    _register_morning_rug(job_queue)
    _register_daily_rug(job_queue)
    _register_midday_update(job_queue)
    _register_report_pair(job_queue)
    _register_updates(job_queue)
