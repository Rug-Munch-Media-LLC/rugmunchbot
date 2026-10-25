"""Intel/ingest channel jobs — one registration step, split from ``_register``.

Flags are imported into THIS namespace on purpose (a test patches one owner per
flag); shared helpers come from ``_core`` so patching ``_core._chat_id`` reaches
every module that schedules. The scanner-only rails (scam watch, security
search, social monitor, tg scraper) need no chat id at all; the posted ones
require flag AND chat id.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.personal_channel import (
    PERSONAL_CHANNEL_ENABLED,
    PERSONAL_CHANNEL_INTERVAL_MINUTES,
    post_personal_channel,
    resolve_personal_chat_id,
)
from rugmunchbot.channels.scam_alerts import run_scam_alerts_publisher
from rugmunchbot.channels.scam_watch import (
    SCAM_WATCH_ENABLED,
    SCAM_WATCH_INTERVAL_MINUTES,
    run_scam_watch,
)
from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._tick import _tick_wrapper
from rugmunchbot.channels.security_search import (
    SECURITY_SEARCH_ENABLED,
    SECURITY_SEARCH_INTERVAL_MINUTES,
    run_security_search,
)
from rugmunchbot.channels.social_monitor import (
    SOCIAL_MONITOR_ENABLED,
    SOCIAL_MONITOR_MENTIONS_INTERVAL_MIN,
    SOCIAL_MONITOR_STATUS_INTERVAL_HOURS,
    SOCIAL_MONITOR_TRENDS_INTERVAL_MIN,
    run_account_status,
    run_mentions,
    run_trends,
)
from rugmunchbot.channels.telegram_news import TG_NEWS_ENABLED, post_tg_news_update
from rugmunchbot.config import (
    SCAM_ALERTS_PUBLISHER_ENABLED,
    SCAM_ALERTS_PUBLISHER_INTERVAL_MINUTES,
)


def _register_tg_news(job_queue: Any) -> None:
    """Routed tg_news job — registered when ANY routed destination resolves.

    Alerts covers the scam-call lane, so a deployment with only alerts+news
    configured still sweeps and routes.
    """
    if TG_NEWS_ENABLED and any(
        _core._chat_id(dest) for dest in ("news", "main", "alpha", "alerts")
    ):
        job_queue.run_repeating(
            _tick_wrapper("channel_tg_news", post_tg_news_update, envelope=True),
            interval=_core._TG_NEWS_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["tg_news"],
            name="channel_tg_news",
        )


def _register_scam_lanes(job_queue: Any) -> None:
    """Scam watch (feeds the scanner) + the RugBrain shill/scam alert stream."""
    if SCAM_WATCH_ENABLED:
        job_queue.run_repeating(
            _tick_wrapper("channel_scam_watch", run_scam_watch, envelope=True),
            interval=SCAM_WATCH_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["scam_watch"],
            name="channel_scam_watch",
        )
    if SCAM_ALERTS_PUBLISHER_ENABLED:
        # Every finding reaches the alerts page; risk >= the configured floor
        # also reaches MAIN. Wrapped so it can never break the news path.
        job_queue.run_repeating(
            _tick_wrapper(
                "channel_scam_alerts_publisher", run_scam_alerts_publisher, envelope=True
            ),
            interval=SCAM_ALERTS_PUBLISHER_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["scam_alerts_publisher"],
            name="channel_scam_alerts_publisher",
        )


def _register_security_search(job_queue: Any) -> None:
    """One governed security-keyword search per tick → RAG news_v2."""
    if SECURITY_SEARCH_ENABLED:
        job_queue.run_repeating(
            _tick_wrapper("channel_security_search", run_security_search, envelope=True),
            interval=SECURITY_SEARCH_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["security_search"],
            name="channel_security_search",
        )


def _register_social_monitor(job_queue: Any) -> None:
    """Three social rails: trends, mentions, account status (63 calls/day)."""
    if not SOCIAL_MONITOR_ENABLED:
        return
    job_queue.run_repeating(
        _tick_wrapper("channel_social_trends", run_trends, envelope=True),
        interval=SOCIAL_MONITOR_TRENDS_INTERVAL_MIN * 60,
        first=_core._FIRST_RUN_SECONDS["social_monitor"],
        name="channel_social_trends",
    )
    job_queue.run_repeating(
        _tick_wrapper("channel_social_mentions", run_mentions, envelope=True),
        interval=SOCIAL_MONITOR_MENTIONS_INTERVAL_MIN * 60,
        first=_core._FIRST_RUN_SECONDS["social_monitor"] + 30,
        name="channel_social_mentions",
    )
    job_queue.run_repeating(
        _tick_wrapper("channel_social_status", run_account_status, envelope=True),
        interval=SOCIAL_MONITOR_STATUS_INTERVAL_HOURS * 3600,
        first=_core._FIRST_RUN_SECONDS["social_monitor"] + 60,
        name="channel_social_status",
    )


def _register_tg_scraper(job_queue: Any) -> None:
    """All-dialogs scraper: scam/shill detection + telegram_social ingestion."""
    from rugmunchbot.channels.tg_scraper import (
        TG_SCRAPER_ENABLED,
        TG_SCRAPER_INTERVAL_MINUTES,
        run_tg_scraper,
    )

    if TG_SCRAPER_ENABLED:
        job_queue.run_repeating(
            _tick_wrapper("channel_tg_scraper", run_tg_scraper, envelope=True),
            interval=TG_SCRAPER_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["scam_watch"] + 30,
            name="channel_tg_scraper",
        )


def _register_personal(job_queue: Any) -> None:
    """PERSONAL channel — needs BOTH the enable flag and a resolvable chat id."""
    if PERSONAL_CHANNEL_ENABLED and resolve_personal_chat_id():
        job_queue.run_repeating(
            _tick_wrapper("channel_personal", post_personal_channel, envelope=True),
            interval=PERSONAL_CHANNEL_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["personal"],
            name="channel_personal",
        )
    else:
        _core.logger.info(
            "channels:disabled",
            job="channel_personal",
            reason=("flag_off" if not PERSONAL_CHANNEL_ENABLED else "chat_id_unresolved"),
        )


def register_intel_jobs(job_queue: Any) -> None:
    """Register the ingest/intel rails (news, scam, security, social, personal)."""
    _register_tg_news(job_queue)
    _register_scam_lanes(job_queue)
    _register_security_search(job_queue)
    _register_social_monitor(job_queue)
    _register_tg_scraper(job_queue)
    _register_personal(job_queue)
