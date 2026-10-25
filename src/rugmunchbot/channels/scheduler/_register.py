"""Channel job registration — the orchestrator, one cadence per sibling module.

``register_channel_jobs`` is deliberately thin: it owns the gate flags (so a
test patches ONE owner per flag), hydrates the dedupe state, then delegates to
the sibling registrars. Each sibling owns one channel family and reads chat ids
through ``_core._chat_id`` at call time, so a patched resolver reaches every
module. The structure gate is shrink-only — a new rail lands in its own module
instead of lengthening this function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.config import (
    ALERTS_CHANNEL_ENABLED,
    ALERTS_CHANNEL_INTERVAL_MINUTES,
    ALPHA_CHANNEL_ENABLED,
    ALPHA_CHANNEL_INTERVAL_HOURS,
    CHANNEL_JOBS_ENABLED,
    SCAN_CHANNEL_ENABLED,
    SCAN_CHANNEL_INTERVAL_MINUTES,
)

if TYPE_CHECKING:
    from telegram.ext import Application


from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._core import (  # noqa: F401
    _FIRST_RUN_SECONDS,
    _TG_NEWS_INTERVAL_MINUTES,
    _chat_id,
    _posted_keys,
    logger,
)
from rugmunchbot.channels.scheduler._posts_news import post_alert_update
from rugmunchbot.channels.scheduler._posts_scan import (
    post_alpha_update,
    post_scan_update,
)
from rugmunchbot.channels.scheduler._tick import (
    _load_channel_state,
    _tick_wrapper,
)


def _hydrate_state(job_queue: Any) -> None:
    """Restore the Redis dedupe sets once, after the queue is really up.

    ``when=10`` and ``misfire_grace_time=300`` (2026-09-01 P0): a 1s delay fired
    before APScheduler finished starting, the run was marked MISSED and every
    restart re-posted the same roundup/drafts. PTB 21 passes ``job_kwargs``
    through, so a late run still executes instead of being dropped.
    """
    if hasattr(job_queue, "run_once"):
        job_queue.run_once(
            _load_channel_state,
            when=10.0,
            name="channel_state_load",
            job_kwargs={"misfire_grace_time": 300},
        )


def _register_lanes(job_queue: Any) -> None:
    """Hand every channel family to the registrar that owns it."""
    from rugmunchbot.channels.scheduler._register_alerts import register_alerts_jobs
    from rugmunchbot.channels.scheduler._register_alpha import register_alpha_jobs
    from rugmunchbot.channels.scheduler._register_content import register_content_jobs
    from rugmunchbot.channels.scheduler._register_daily import register_daily_jobs
    from rugmunchbot.channels.scheduler._register_intel import register_intel_jobs
    from rugmunchbot.channels.scheduler._register_kol import register_kol_jobs
    from rugmunchbot.channels.scheduler._register_lanes import register_lane_jobs

    register_alpha_jobs(job_queue, alpha_enabled=ALPHA_CHANNEL_ENABLED)
    register_alerts_jobs(job_queue, alerts_enabled=ALERTS_CHANNEL_ENABLED)
    register_kol_jobs(job_queue, alerts_enabled=ALERTS_CHANNEL_ENABLED)
    register_content_jobs(job_queue, alerts_enabled=ALERTS_CHANNEL_ENABLED)
    register_intel_jobs(job_queue)
    register_daily_jobs(job_queue)
    register_lane_jobs(job_queue)


def _register_core_repeaters(job_queue: Any) -> None:
    """The three always-on channel digests (alerts / alpha / scans).

    The legacy 4h ``channel_news`` digest stays retired: the routed tg_news
    lane owns news and a second job would double-post the same story.
    """
    if ALERTS_CHANNEL_ENABLED and _core._chat_id("alerts"):
        job_queue.run_repeating(
            _tick_wrapper("channel_alerts", post_alert_update),
            interval=ALERTS_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["alerts"],
            name="channel_alerts",
        )
    if ALPHA_CHANNEL_ENABLED and _core._chat_id("alpha"):
        job_queue.run_repeating(
            _tick_wrapper("channel_alpha", post_alpha_update),
            interval=ALPHA_CHANNEL_INTERVAL_HOURS * 3600,
            first=_FIRST_RUN_SECONDS["alpha"],
            name="channel_alpha",
        )
    if SCAN_CHANNEL_ENABLED and _core._chat_id("scans"):
        job_queue.run_repeating(
            _tick_wrapper("channel_scans", post_scan_update),
            interval=SCAN_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["scans"],
            name="channel_scans",
        )


def register_channel_jobs(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the channel poster jobs on the PTB JobQueue (fail-open).

    Skips a channel when its job is disabled or its chat id is unset, so a
    half-configured deployment still gets the channels it can post to.
    """
    if not CHANNEL_JOBS_ENABLED:
        logger.info("channels:job scheduling disabled (RMI_CHANNELS_ENABLED=false)")
        return
    job_queue = app.job_queue
    if job_queue is None:
        logger.info("channels:no job queue — skipping channel jobs")
        return
    _hydrate_state(job_queue)
    _register_lanes(job_queue)
    _register_core_repeaters(job_queue)
    logger.info(
        "channels:channel jobs registered (news/alerts/alpha/scans/tg_news/scam_watch/personal/channel_morning_rug/channel_midday_update/updates/shill_watch/kol_scoreboard/scammer_warning/daily_quality/ct_shill_watch/kol_daily_refresh)"
    )
