"""Intel/ingest channel jobs — one registration step, split from ``_register``.

Extracted verbatim out of ``register_channel_jobs``: the structure gate is
shrink-only, so a new rail lands in its own module. Flags are imported into
THIS namespace on purpose (a test patches one owner per flag); shared helpers
come from ``_core`` so patching ``_core._chat_id`` reaches every module that
schedules.

The scanner-only rails (scam watch, security search, social monitor, tg
scraper) need no chat id at all; the posted ones require flag AND chat id.
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


def register_intel_jobs(job_queue: Any) -> None:
    """Register the ingest/intel rails (news, scam, security, social, personal)."""

    # Routed tg_news job: registered when ANY routed destination resolves
    # (news / main @cryptorugmuncher / alpha / alerts) — the post path routes
    # each scored story to exactly one destination via ``route_news``. The
    # alerts destination covers the scam-call lane (2026-08-29): scam-call
    # digests post to @rmialerts, so a deployment with only alerts+news
    # configured still sweeps and routes.
    if TG_NEWS_ENABLED and any(
        _core._chat_id(dest) for dest in ("news", "main", "alpha", "alerts")
    ):
        job_queue.run_repeating(
            _tick_wrapper("channel_tg_news", post_tg_news_update, envelope=True),
            interval=_core._TG_NEWS_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["tg_news"],
            name="channel_tg_news",
        )
    if SCAM_WATCH_ENABLED:
        # Feeds the scanner (no bot chat id needed — nothing is posted).
        job_queue.run_repeating(
            _tick_wrapper("channel_scam_watch", run_scam_watch, envelope=True),
            interval=SCAM_WATCH_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["scam_watch"],
            name="channel_scam_watch",
        )

    if SCAM_ALERTS_PUBLISHER_ENABLED:
        # Shill/scam alerts from the RugBrain watchlist stream. Every finding
        # reaches the alerts page; risk >= RMI_SCAM_ALERTS_MAIN_RISK also
        # reaches the MAIN channel. Wrapped so it can never break the news path.
        job_queue.run_repeating(
            _tick_wrapper(
                "channel_scam_alerts_publisher", run_scam_alerts_publisher, envelope=True
            ),
            interval=SCAM_ALERTS_PUBLISHER_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["scam_alerts_publisher"],
            name="channel_scam_alerts_publisher",
        )
    if SECURITY_SEARCH_ENABLED:
        # Security keyword search (2026-09-02): ONE GetXAPI advanced_search
        # per tick against a rotating security query → RAG news_v2 (no bot
        # chat id needed — nothing is posted). 48 calls/day = $0.048/day.
        job_queue.run_repeating(
            _tick_wrapper("channel_security_search", run_security_search, envelope=True),
            interval=SECURITY_SEARCH_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["security_search"],
            name="channel_security_search",
        )
    if SOCIAL_MONITOR_ENABLED:
        # Social monitor (2026-09-02): trending topics + @cryptorugmuncher
        # mention monitoring via GetXAPI (no bot chat id needed — nothing is
        # posted). Three rails:
        #   * Trends — ONE /trends per tick, crypto-filtered, Redis-cached
        #     30 min. 24 calls/day = $0.024/day.
        #   * Mentions — ONE /user/mentions per tick for @cryptorugmuncher →
        #     RAG news_v2. 24 calls/day = $0.024/day.
        #   * Account status — ONE /user/status per monitored account per day
        #     (15 accounts), WARNING on suspension. 15 calls/day = $0.015/day.
        # Total: 63 calls/day = $0.063/day.
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
    from rugmunchbot.channels.tg_scraper import (
        TG_SCRAPER_ENABLED,
        TG_SCRAPER_INTERVAL_MINUTES,
        run_tg_scraper,
    )

    if TG_SCRAPER_ENABLED:
        # All-dialogs scraper: scam/shill detection + telegram_social RAG
        # ingestion (no bot chat id needed — nothing is posted).
        job_queue.run_repeating(
            _tick_wrapper("channel_tg_scraper", run_tg_scraper, envelope=True),
            interval=TG_SCRAPER_INTERVAL_MINUTES * 60,
            first=_core._FIRST_RUN_SECONDS["scam_watch"] + 30,
            name="channel_tg_scraper",
        )
    # PERSONAL is a wired channel with a real source (personal_channel.py).
    # It is gated by TWO conditions: the enable flag AND a resolvable chat id.
    # When either is false the job is never registered, which previously left
    # no trace at all and read as a dead channel in the cadence audit
    # (2026-10-17). Record the intentional skip explicitly.
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
