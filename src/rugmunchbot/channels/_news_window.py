"""Timeframe window computation for the branded daily posts.

Both flagship posts ("The Morning Rug" 07:00 ET and "Midday Market Update"
12:30 ET) rank stories over the window BETWEEN the two posts, not over a flat
24h — so the window is derived from the durable scheduler state
(``channels.scheduler_state`` last-run hash, Redis-backed, survives restarts)
rather than from a hardcoded span. A missing or never-run counterpart falls
back to 12h; a restart gap can never pull a multi-day window.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("rmi_bot.channels.news_window")

#: Fallback window (hours) when the counterpart job has never run.
DEFAULT_WINDOW_HOURS = 12.0

#: Hard cap — a restart gap must never pull a multi-day window.
MAX_WINDOW_HOURS = 48.0

#: Scheduler job names (new + legacy) used for window computation.
MORNING_JOB_NAMES = ("channel_morning_rug", "daily_market_summary")
MIDDAY_JOB_NAMES = (
    "channel_midday_update",
    "channel_daily_rug_update",
    "daily_market_summary_midday",
)


def window_hours_since(
    job_names: tuple[str, ...], fallback_hours: float = DEFAULT_WINDOW_HOURS
) -> float:
    """Hours since the counterpart job last ran (clamped, fail-open).

    Reads the durable scheduler state; unknown/never-run jobs fall back to
    ``fallback_hours``. The result is clamped to
    ``[0.5, MAX_WINDOW_HOURS]`` so a clock gap can never produce an empty
    or multi-day window.
    """
    try:
        from rugmunchbot.channels.scheduler_state import get_state

        state = get_state()
        for name in job_names:
            last = state.last_run(name)
            if last is not None:
                from datetime import UTC, datetime

                hours = (datetime.now(UTC) - last).total_seconds() / 3600.0
                if hours <= 0:
                    return fallback_hours
                return max(0.5, min(MAX_WINDOW_HOURS, hours))
    except Exception as exc:
        logger.debug("news_rerank:window_lookup_failed err=%s", str(exc)[:120])
    return fallback_hours


def morning_window_hours() -> float:
    """Morning Rug timeframe: since the last Midday Update (fallback 12h)."""
    return window_hours_since(MIDDAY_JOB_NAMES)


def midday_window_hours() -> float:
    """Midday Update timeframe: since the last Morning Rug (fallback 12h)."""
    return window_hours_since(MORNING_JOB_NAMES)
