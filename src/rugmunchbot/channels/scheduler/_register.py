from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.daily_market_summary import (
    DAILY_MARKET_SUMMARY_ENABLED,
    post_daily_market_summary,
    summary_run_time,
)
from rugmunchbot.channels.personal_channel import (
    PERSONAL_CHANNEL_ENABLED,
    PERSONAL_CHANNEL_INTERVAL_MINUTES,
    post_personal_channel,
    resolve_personal_chat_id,
)
from rugmunchbot.channels.scam_watch import (
    SCAM_WATCH_ENABLED,
    SCAM_WATCH_INTERVAL_MINUTES,
    run_scam_watch,
)
from rugmunchbot.channels.telegram_news import (
    TG_NEWS_ENABLED,
    post_tg_news_update,
)
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
    from telegram.ext import Application, ContextTypes


from rugmunchbot.channels.scheduler._core import (  # noqa: F401
    _FIRST_RUN_SECONDS,
    _TG_NEWS_INTERVAL_MINUTES,
    _chat_id,
    _posted_keys,
    logger,
)
from rugmunchbot.channels.scheduler._posts_dislocation import (
    build_dislocation_post,
)
from rugmunchbot.channels.scheduler._posts_lp_expiry import (
    build_lp_expiry_post,
)
from rugmunchbot.channels.scheduler._content_rotation import CONTENT_SLOTS
from rugmunchbot.channels.scheduler._posts_news import (
    post_alert_update,
)
from rugmunchbot.channels.scheduler._posts_scan import (
    post_alpha_update,
    post_scan_update,
)
from rugmunchbot.channels.scheduler._tick import (
    _load_channel_state,
    _tick_wrapper,
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

    # Durability: hydrate the dedupe sets + last-run timestamps from Redis
    # once at startup (one-shot job) so a restart resumes cleanly. Guarded
    # for queues that predate PTB run_once (and test fakes).
    if hasattr(job_queue, "run_once"):
        job_queue.run_once(_load_channel_state, when=1.0, name="channel_state_load")

    # DEPRECATED 2026-08-16 — legacy 4h news digest job (``channel_news``).
    # Superseded by the routed tg_news path (``channel_tg_news`` below):
    # ``post_tg_news_update`` + ``channels/router.py::route_news`` now own the
    # news channel, so keeping BOTH jobs registered would double-post the same
    # story (the two jobs use different dedupe prefixes: ``news:`` here vs
    # ``tgnews:``/``tgmain:``/``tgalpha:`` in the tg_news lane). Commented out;
    # remove the ``post_news_update`` function in a follow-up.
    # if NEWS_CHANNEL_ENABLED and _chat_id("news"):
    #     job_queue.run_repeating(
    #         _tick_wrapper("channel_news", post_news_update),
    #         interval=NEWS_CHANNEL_INTERVAL_HOURS * 3600,
    #         first=_FIRST_RUN_SECONDS["news"],
    #         name="channel_news",
    #     )
    # Funding-dislocation monitor (win 32): EXTREME cross-venue basis finds.
    if ALERTS_CHANNEL_ENABLED and _chat_id("alerts") and hasattr(job_queue, "run_repeating"):
        async def _dislo_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                text = await build_dislocation_post()
                if text and _chat_id("alerts"):
                    from rugmunchbot.channels.scheduler._core import _safe_send

                    await _safe_send(ctx.bot, _chat_id("alerts"), text)
            except Exception as exc:
                logger.warning("dislocation_tick failed: %s", str(exc)[:120])

        job_queue.run_repeating(
            _dislo_tick,
            interval=int(os.getenv("DISLOCATION_ALERT_INTERVAL_H", "2")) * 3600,
            first=600,
            name="channel_dislocation",
        )

    # RugBrain content-distribution (2026-08-24): RAG-ranked stories ->
    # quality-written posts routed to main/news/alerts (+ scans via its own
    # lane). Drafts come from rugbrain /api/v1/content/drafts; dedupe via
    # _remember("rag", ...); main posts mirror to feed.rugmunch.io.
    if os.getenv("RMI_RAG_POSTS_ENABLED", "true").lower() in ("1", "true", "yes") and hasattr(
        job_queue, "run_repeating"
    ):
        from rugmunchbot.channels.scheduler._posts_rag import post_rag_drafts

        job_queue.run_repeating(
            _tick_wrapper("channel_rag", post_rag_drafts),
            interval=int(os.getenv("RMI_RAG_POSTS_INTERVAL_MIN", "45")) * 60,
            first=900,
            name="channel_rag",
        )

    # Social-velocity spike monitor (win 36): narrative formation detection.
    if ALERTS_CHANNEL_ENABLED and _chat_id("alerts") and hasattr(job_queue, "run_repeating"):
        async def _social_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                text = await build_social_spike_post()
                if text and _chat_id("alerts"):
                    from rugmunchbot.channels.scheduler._core import _safe_send

                    await _safe_send(ctx.bot, _chat_id("alerts"), text)
            except Exception as exc:
                logger.warning("social_spike_tick failed: %s", str(exc)[:120])

        job_queue.run_repeating(
            _social_tick,
            interval=int(os.getenv("SOCIAL_SPIKE_INTERVAL_MIN", "90")) * 60,
            first=900,
            name="channel_social_spikes",
        )

    # LP-expiry monitor (win 35): warn before liquidity unlocks.
    if ALERTS_CHANNEL_ENABLED and _chat_id("alerts") and hasattr(job_queue, "run_repeating"):
        async def _lp_expiry_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                text = await build_lp_expiry_post()
                if text and _chat_id("alerts"):
                    from rugmunchbot.channels.scheduler._core import _safe_send

                    await _safe_send(ctx.bot, _chat_id("alerts"), text)
            except Exception as exc:
                logger.warning("lp_expiry_tick failed: %s", str(exc)[:120])

        job_queue.run_repeating(
            _lp_expiry_tick,
            interval=int(os.getenv("LP_EXPIRY_INTERVAL_H", "6")) * 3600,
            first=1800,
            name="channel_lp_expiry",
        )

    # Content rotation: multiple premium content types to main daily (win 32+)
    for slot in CONTENT_SLOTS:
        slot_type = slot["type"]
        hour = int(slot["time_utc"].split(":")[0])
        minute = int(slot["time_utc"].split(":")[1]) if ":" in slot["time_utc"] else 0

        async def _content_tick(ctx: ContextTypes.DEFAULT_TYPE, _type=slot_type) -> None:
            try:
                logger.info("content_rotation_tick type=%s", _type)
                # Each content type has its own generator; wire them here
                # as they're implemented. Fail-open per type.
            except Exception as exc:
                logger.warning("content_rotation %s failed: %s", _type, str(exc)[:120])

        job_queue.run_repeating(
            _content_tick,
            interval=86400,  # daily
            first=hour * 3600 + minute * 60,
            name=f"content_{slot_type}",
        )

    if ALERTS_CHANNEL_ENABLED and _chat_id("alerts"):
        job_queue.run_repeating(
            _tick_wrapper("channel_alerts", post_alert_update),
            interval=ALERTS_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["alerts"],
            name="channel_alerts",
        )
    if ALPHA_CHANNEL_ENABLED and _chat_id("alpha"):
        job_queue.run_repeating(
            _tick_wrapper("channel_alpha", post_alpha_update),
            interval=ALPHA_CHANNEL_INTERVAL_HOURS * 3600,
            first=_FIRST_RUN_SECONDS["alpha"],
            name="channel_alpha",
        )
    if SCAN_CHANNEL_ENABLED and _chat_id("scans"):
        job_queue.run_repeating(
            _tick_wrapper("channel_scans", post_scan_update),
            interval=SCAN_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["scans"],
            name="channel_scans",
        )
    # Routed tg_news job: registered when ANY routed destination resolves
    # (news / main @cryptorugmuncher / alpha) — the post path routes each
    # scored story to exactly one destination via ``route_news``.
    if TG_NEWS_ENABLED and any(_chat_id(dest) for dest in ("news", "main", "alpha")):
        job_queue.run_repeating(
            _tick_wrapper("channel_tg_news", post_tg_news_update, envelope=True),
            interval=_TG_NEWS_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["tg_news"],
            name="channel_tg_news",
        )
    if SCAM_WATCH_ENABLED:
        # Feeds the scanner (no bot chat id needed — nothing is posted).
        job_queue.run_repeating(
            _tick_wrapper("channel_scam_watch", run_scam_watch, envelope=True),
            interval=SCAM_WATCH_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["scam_watch"],
            name="channel_scam_watch",
        )
    if PERSONAL_CHANNEL_ENABLED and resolve_personal_chat_id():
        job_queue.run_repeating(
            _tick_wrapper("channel_personal", post_personal_channel, envelope=True),
            interval=PERSONAL_CHANNEL_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["personal"],
            name="channel_personal",
        )
    # Daily Market Summary — 7:00 AM America/New_York, 7 days/week, on the
    # main channel (@cryptorugmuncher). Writes an editorial market breakdown
    # via ``chat_with_fallback(messages, "long")`` and posts it to the main
    # channel + the rugmunch.io feed (both fail-open) — see
    # ``channels/daily_market_summary.py``. Env knobs:
    # ``RMI_DAILY_MARKET_SUMMARY_ENABLED`` / ``_TIME`` / ``_TZ``.
    if DAILY_MARKET_SUMMARY_ENABLED and _chat_id("main"):
        job_queue.run_daily(
            _tick_wrapper("daily_market_summary", post_daily_market_summary, envelope=True),
            time=summary_run_time(),
            days=tuple(range(7)),
            name="daily_market_summary",
        )
    logger.info(
        "channels:channel jobs registered (news/alerts/alpha/scans/tg_news/scam_watch/personal/daily_market_summary)"
    )
