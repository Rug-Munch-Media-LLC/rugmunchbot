"""Daily-report channel jobs — one registration step, split from ``_register``.

Extracted verbatim out of ``register_channel_jobs``: every job here is a
``run_daily`` on the main channel, so a whole cadence reads in one place and
the structure gate stays shrink-only.

The three flagship editions (Morning Rug / Midday Report / Evening Wrap) are
each a different facet; the retired Midday Update stays behind its opt-in
knob instead of being deleted, so a cadence can be turned back on without a
code change.
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


def register_daily_jobs(job_queue: Any) -> None:
    """Register the once-a-day main-channel editions and the changelog."""

    # The Morning Rug (branded 2026-08-31, formerly "Daily Market Summary") — 7:00 AM America/New_York, 7 days/week, on the
    # main channel (@cryptorugmuncher). Writes an editorial market breakdown
    # via ``chat_with_fallback(messages, "long")`` and posts it to the main
    # channel + the rugmunch.io feed (both fail-open) — see
    # ``channels/daily_market_summary.py``. Env knobs:
    # ``RMI_DAILY_MARKET_SUMMARY_ENABLED`` / ``_TIME`` / ``_TZ``.
    if DAILY_MARKET_SUMMARY_ENABLED and _core._chat_id("main"):
        job_queue.run_daily(
            _tick_wrapper("channel_morning_rug", post_daily_market_summary, envelope=True),
            time=summary_run_time(),
            days=tuple(range(7)),
            name="channel_morning_rug",
        )
        # Midday edition (2026-08-26): same pipeline, fresher data — the
        # closing block auto-labels it "Midday edition" by post hour.
        # DISABLED by default (2026-10-17): it fires at 13:00 ET, 30 minutes
        # after the Midday Market Report (12:30 ET), repeating the same
        # market stories outside the three-edition dedupe. Same rationale as
        # the retired Midday Update. Re-enable with
        # ``RMI_MORNING_RUG_MIDDAY_ENABLED=true``.
        # NOTE: _dtime alias — the function-local `from datetime import time
        # as _time` further down makes _time unbound here (scoping).
        from datetime import time as _dtime

        _midday = os.getenv("RMI_DAILY_MARKET_BRIEF_MIDDAY_TIME", "13:00")
        try:
            _mh, _mm = (int(x) for x in _midday.split(":", 1))
        except ValueError:
            _mh, _mm = 13, 0
        if os.getenv("RMI_MORNING_RUG_MIDDAY_ENABLED", "false").lower() in ("1", "true", "yes"):
            job_queue.run_daily(
                _tick_wrapper(
                    "channel_morning_rug_midday", post_daily_market_summary, envelope=True
                ),
                time=_dtime(hour=_mh, minute=_mm, tzinfo=summary_run_time().tzinfo),
                days=tuple(range(7)),
                name="channel_morning_rug_midday",
            )
    # The Daily Rug — Morning Market Brief (2026-10-16, operator doctrine):
    # the flagship ONCE-DAILY longform morning edition on the main channel
    # (@cryptorugmuncher). Best stories from the previous day + a
    # "Things to look out for" section. A date guard makes the job
    # idempotent — exactly one post per calendar day, no matter how many
    # times the scheduler ticks or the process restarts. Mirrored to
    # feed.rugmunch.io with a deep link to the exact post.
    # Env knobs: ``RMI_DAILY_RUG_ENABLED`` / ``_TIME`` / ``_TZ``.
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
    # Midday Market Update (branded 2026-08-31, formerly "Daily Rug Update"):
    # midday rug/scam intelligence brief on the main channel. DISABLED by
    # default (2026-10-16) — the operator doctrine is ONE flagship daily
    # post; the Midday Update duplicated the Morning Rug's stories and read
    # as filler. Re-enable with ``RMI_DAILY_RUG_UPDATE_ENABLED=true``.
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
    # ── The three daily reports (2026-10-17, operator doctrine) ──────────
    # THREE distinct main-channel reports, each a DIFFERENT facet, no
    # duplicated content (three-way dedup in channels/_edition_dedup.py):
    #   07:30  The Daily Rug — Morning Market Brief  (crypto stories)
    #   12:30  Midday Market Report                  (macro + broader markets)
    #   20:00  Evening Wrap                          (scams, shills, rugs)
    # Each is idempotent (date guard) and mirrors to Ghost longform.
    from rugmunchbot.channels.midday_market_report import (
        MIDDAY_REPORT_ENABLED,
        midday_report_run_time,
        post_midday_report,
    )

    if MIDDAY_REPORT_ENABLED and _core._chat_id("main"):
        job_queue.run_daily(
            _tick_wrapper("channel_midday_report", post_midday_report, envelope=True),
            time=midday_report_run_time(),
            days=tuple(range(7)),
            name="channel_midday_report",
        )
    from rugmunchbot.channels.evening_wrap import (
        EVENING_WRAP_ENABLED,
        evening_wrap_run_time,
        post_evening_wrap,
    )

    if EVENING_WRAP_ENABLED and _core._chat_id("main"):
        job_queue.run_daily(
            _tick_wrapper("channel_evening_wrap", post_evening_wrap, envelope=True),
            time=evening_wrap_run_time(),
            days=tuple(range(7)),
            name="channel_evening_wrap",
        )
    # @rmiupdates changelog — daily system-progress digest (strategy v2).
    # Fires once per day at UPDATES_POST_HOUR_UTC; the poster dedupes per
    # calendar date, so the job no-ops when the channel id is unresolved.
    from datetime import time as _time

    from rugmunchbot.channels.scheduler._posts_updates import post_updates_digest

    # UPDATES is wired (post_updates_digest) and wrapped, but only registered
    # when the flag is on — record the skip so the channel is never silently
    # absent from the scheduler telemetry (2026-10-17).
    if UPDATES_CHANNEL_ENABLED:
        job_queue.run_daily(
            _tick_wrapper("channel_updates", post_updates_digest, envelope=True),
            time=_time(hour=UPDATES_POST_HOUR_UTC, minute=0),
            days=tuple(range(7)),
            name="channel_updates",
        )
    else:
        _core.logger.info("channels:disabled", job="channel_updates", reason="flag_off")
