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
from rugmunchbot.channels.scam_alerts import run_scam_alerts_publisher
from rugmunchbot.channels.scam_watch import (
    SCAM_WATCH_ENABLED,
    SCAM_WATCH_INTERVAL_MINUTES,
    run_scam_watch,
)
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
    SCAM_ALERTS_PUBLISHER_ENABLED,
    SCAM_ALERTS_PUBLISHER_INTERVAL_MINUTES,
    SCAN_CHANNEL_ENABLED,
    SCAN_CHANNEL_INTERVAL_MINUTES,
    UPDATES_CHANNEL_ENABLED,
    UPDATES_POST_HOUR_UTC,
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
from rugmunchbot.channels.scheduler._posts_news import (
    post_alert_update,
)
from rugmunchbot.channels.scheduler._posts_scan import (
    post_alpha_update,
    post_scan_update,
)

# 2026-08-25 P0 fix: build_social_spike_post was CALLED by _social_tick but
# never imported → NameError on every social-spike tick (caught + logged as
# a warning, so the job silently never worked since registration).
from rugmunchbot.channels.scheduler._posts_social_spikes import (
    build_social_spike_post,
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
    #
    # 2026-09-01 P0 fix: when=1.0 fired BEFORE the PTB job queue finished
    # starting, so APScheduler marked the run MISSED (~5s late) and never
    # executed it — the Redis dedupe state was NEVER restored and every
    # restart re-posted the same roundup/drafts to @cryptorugmuncher
    # (observed 3x on 2026-08-31, one per deploy restart). when=10 gives
    # the queue time to come up; misfire_grace_time=300 (via job_kwargs —
    # PTB 21 passes it through to APScheduler) means a late job still runs
    # instead of being dropped.
    if hasattr(job_queue, "run_once"):
        job_queue.run_once(
            _load_channel_state,
            when=10.0,
            name="channel_state_load",
            job_kwargs={"misfire_grace_time": 300},
        )

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
    # 2026-08-29 doctrine reroute: funding dislocation is ALPHA content
    # (data-driven market signal — CONTENT-STRATEGY-CANONICAL lists it under
    # ALPHA/WHALES), NOT a rug/exploit/scam detection. It posts to @rmialpha;
    # @rmialerts carries imminent-rug/exploit/scam detections ONLY.
    if ALPHA_CHANNEL_ENABLED and _chat_id("alpha") and hasattr(job_queue, "run_repeating"):

        async def _dislo_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                text = await build_dislocation_post()
                if text and _chat_id("alpha"):
                    from rugmunchbot.channels.scheduler._core import (
                        _post_photo_aware,
                        _safe_send,
                    )

                    await _post_photo_aware(
                        ctx.bot, _chat_id("alpha"), text, _safe_send, content_type="alpha"
                    )
            except Exception as exc:
                logger.warning("dislocation_tick failed: %s", str(exc)[:120])

        job_queue.run_repeating(
            _tick_wrapper("channel_dislocation", _dislo_tick, envelope=True),
            interval=int(os.getenv("DISLOCATION_ALERT_INTERVAL_H", "2")) * 3600,
            first=600,
            name="channel_dislocation",
        )

    # Whale + Alpha (2026-08-26, rerouted 2026-08-29): multi-chain whale
    # moves from the firehose's whale_transfers collection → ALPHA channel
    # every 30 min. Channel doctrine v3: whale moves are "worth paying for"
    # alpha — they NEVER belong on @rmialerts (that channel is exploit /
    # scam / shill-campaign detections only). Fail-open.
    if ALPHA_CHANNEL_ENABLED and _chat_id("alpha") and hasattr(job_queue, "run_repeating"):
        from rugmunchbot.channels.scheduler._posts_whales import build_whale_post

        async def _whale_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                text = await build_whale_post()
                alpha_chat = _chat_id("alpha")
                if text and alpha_chat:
                    from rugmunchbot.channels.scheduler._core import _post_photo_aware, _safe_send

                    # HARD quality gate (2026-08-31): whale alpha is paid-product
                    # content — lint → autofix → skip, same as every other lane.
                    # Image doctrine: resolve before the gate (branded placeholder
                    # fallback) so the gate can never block on a missing image.
                    from rugmunchbot.support.image_policy import resolve_post_image
                    from rugmunchbot.support.quality_gate import pre_post_quality_gate

                    resolved_image = await resolve_post_image("alpha", logger=logger)
                    gate = await pre_post_quality_gate(
                        text,
                        image_url=resolved_image,
                        source="whale_alerts",
                        kind="whale_alert",
                        min_words=8,  # structured alert format — terse by design
                        require_image=True,
                        allow_placeholder_image=True,
                        logger=logger,
                    )
                    if not gate.ok:
                        logger.warning(
                            "whale_alerts_tick blocked by quality gate: %s",
                            [v.rule for v in gate.violations],
                        )
                        return
                    await _post_photo_aware(
                        ctx.bot, alpha_chat, gate.text, _safe_send, content_type="whale"
                    )
            except Exception as exc:
                logger.warning("whale_alerts_tick failed: %s", str(exc)[:120])

        job_queue.run_repeating(
            _tick_wrapper("channel_whale_alerts", _whale_tick, envelope=True),
            interval=int(os.getenv("WHALE_ALERT_INTERVAL_MIN", "30")) * 60,
            first=180,
            name="channel_whale_alerts",
        )

    # Shill-campaign watch (2026-08-29, quality overhaul deliverable 4):
    # coordinated shill detection (same token pushed by many KOL/alpha
    # accounts in a window) → ALERTS channel. High-signal only — the alerts
    # channel doctrine admits exploit/scam/rug + shill-campaign detections,
    # never routine moves.
    if (
        ALERTS_CHANNEL_ENABLED
        and _chat_id("alerts")
        and hasattr(job_queue, "run_repeating")
        and os.getenv("RMI_SHILL_WATCH_ENABLED", "true").lower() in ("1", "true", "yes")
    ):
        from rugmunchbot.channels.shill_watch import SHILL_INTERVAL_MIN, run_shill_watch

        job_queue.run_repeating(
            _tick_wrapper("channel_shill_watch", run_shill_watch, envelope=True),
            interval=SHILL_INTERVAL_MIN * 60,
            first=300,
            name="channel_shill_watch",
        )

    # Weekly KOL Wall of Shame (2026-09-04): every Sunday 18:00 UTC, post the
    # top-10 worst KOLs (by scams shilled / reach / damage) to @rmialerts and
    # mirror to feed.rugmunch.io. Reads ``data/kol/wall_of_shame.json``.
    if (
        ALERTS_CHANNEL_ENABLED
        and _chat_id("alerts")
        and hasattr(job_queue, "run_daily")
        and os.getenv("RMI_KOL_WALL_OF_SHAME_ENABLED", "true").lower() in ("1", "true", "yes")
    ):
        from datetime import UTC, time as _wos_time

        from rugmunchbot.channels.shill_watch import post_kol_wall_of_shame

        job_queue.run_daily(
            _tick_wrapper("channel_kol_wall_of_shame", post_kol_wall_of_shame, envelope=True),
            time=_wos_time(hour=18, minute=0, tzinfo=UTC),
            days=(6,),  # Sunday (Python weekday: Monday=0 … Sunday=6)
            name="channel_kol_wall_of_shame",
        )

    # Shill-campaign auto-poster (2026-08-31): rugbrain's clustered shill
    # campaigns (POST /rag/shill-campaigns) → main channel with screenshot +
    # commentary. Dedup on campaign signature; fail-open. Env knobs:
    # ``RMI_SHILL_CAMPAIGN_POST_ENABLED`` / ``_INTERVAL_MIN`` / ``_MAX_POSTS``.
    if (
        os.getenv("RMI_SHILL_CAMPAIGN_POST_ENABLED", "true").lower() in ("1", "true", "yes")
        and _chat_id("main")
        and hasattr(job_queue, "run_repeating")
    ):
        from rugmunchbot.channels.shill_campaign_post import (
            SHILL_CAMPAIGN_INTERVAL_MIN,
            run_shill_campaign_poster,
        )

        job_queue.run_repeating(
            _tick_wrapper("channel_shill_campaign", run_shill_campaign_poster, envelope=True),
            interval=SHILL_CAMPAIGN_INTERVAL_MIN * 60,
            first=480,
            name="channel_shill_campaign",
        )

    # Scanner-alert rail (2026-08-31): polls RugBrain live detections
    # (GET /api/v1/alerts/recent) → @rmialerts. Wires the scanner →
    # rugbrain → bot → Telegram pipeline end-to-end. Env knobs:
    # ``RMI_SCANNER_ALERTS_ENABLED`` / ``_INTERVAL_MIN``. Fail-open.
    if (
        ALERTS_CHANNEL_ENABLED
        and _chat_id("alerts")
        and hasattr(job_queue, "run_repeating")
        and os.getenv("RMI_SCANNER_ALERTS_ENABLED", "true").lower() in ("1", "true", "yes")
    ):
        from rugmunchbot.channels.scanner_alerts import (
            SCANNER_ALERT_INTERVAL_MIN,
            run_scanner_alert_watch,
        )

        job_queue.run_repeating(
            _tick_wrapper("channel_scanner_alerts", run_scanner_alert_watch, envelope=True),
            interval=SCANNER_ALERT_INTERVAL_MIN * 60,
            first=240,
            name="channel_scanner_alerts",
        )

    # KOL Scoreboard (2026-08-29, renamed from KOL Hall of Shame): track
    # KOL calls + deleted tweets + name changes + wallets from real X data
    # (ApiPass) → per-KOL report cards → ALERTS channel, deduped per card
    # per day. Hall of Shame = repeat-scammer subset. Budget-bounded by the
    # shared ApiPass ≤6/hr limiter; fail-open when the key is unconfigured.
    if (
        ALERTS_CHANNEL_ENABLED
        and _chat_id("alerts")
        and hasattr(job_queue, "run_repeating")
        and os.getenv(
            "RMI_KOL_SCOREBOARD_ENABLED",
            os.getenv("RMI_KOL_SHAME_ENABLED", "true"),
        ).lower()
        in ("1", "true", "yes")
    ):
        from rugmunchbot.channels.kol_scoreboard import (
            KOL_SCOREBOARD_INTERVAL_MIN,
            run_kol_scoreboard_watch,
        )

        job_queue.run_repeating(
            _tick_wrapper("channel_kol_scoreboard", run_kol_scoreboard_watch, envelope=True),
            interval=KOL_SCOREBOARD_INTERVAL_MIN * 60,
            first=360,
            name="channel_kol_scoreboard",
        )

    # CT-timeline shill watch (2026-09-02): rotates the curated
    # influential_accounts registry via ApiPass (2 governed creates per
    # account), flags token-promoting tweets with engagement, confirms via
    # scanner + TG cross-ref, captures screenshot evidence, auto-adds to the
    # Wall of Shame, and embeds shills into the Qdrant social_intel
    # collection. Env knobs: RMI_CT_SHILL_ENABLED / _INTERVAL_MIN /
    # _ACCOUNTS_PER_TICK / _MAX_ADDS.
    if os.getenv("RMI_CT_SHILL_ENABLED", "true").lower() in ("1", "true", "yes") and hasattr(
        job_queue, "run_repeating"
    ):
        from rugmunchbot.channels.ct_shill_watch import (
            CT_SHILL_INTERVAL_MIN,
            run_ct_shill_watch,
        )

        job_queue.run_repeating(
            _tick_wrapper("channel_ct_shill_watch", run_ct_shill_watch, envelope=True),
            interval=CT_SHILL_INTERVAL_MIN * 60,
            first=420,
            name="channel_ct_shill_watch",
        )

    # Daily KOL refresh (2026-09-02): rescores every Wall of Shame entry from
    # accumulated shill history (tokens x coordination x recency) and
    # discovers NEW KOL candidates from retweeters of known shill tweets
    # (one governed ApiPass pull). Env knobs: RMI_KOL_DAILY_REFRESH_ENABLED /
    # _INTERVAL_MIN / _MAX_RETWEETER_PULLS.
    if os.getenv("RMI_KOL_DAILY_REFRESH_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    ) and hasattr(job_queue, "run_repeating"):
        from rugmunchbot.channels.ct_shill_watch import (
            KOL_DAILY_INTERVAL_MIN,
            run_kol_daily_refresh,
        )

        job_queue.run_repeating(
            _tick_wrapper("channel_kol_daily_refresh", run_kol_daily_refresh, envelope=True),
            interval=KOL_DAILY_INTERVAL_MIN * 60,
            first=600,
            name="channel_kol_daily_refresh",
        )

    # Scammer-warning watch (2026-08-29): when an account in a seeded
    # scammer group (e.g. the ZachXBT-named ``solana_syndicate`` network)
    # shills a token, post the warning to @rmialerts (+ main when the call
    # classifies shill/rug, mirrored to feed.rugmunch.io). Group membership
    # is evidence-backed in known_scammers.json; ZachXBT is cited as source.
    if (
        ALERTS_CHANNEL_ENABLED
        and _chat_id("alerts")
        and hasattr(job_queue, "run_repeating")
        and os.getenv("RMI_SCAMMER_WARNING_ENABLED", "true").lower() in ("1", "true", "yes")
    ):
        from rugmunchbot.channels.scammer_warning import (
            SCAMMER_WARNING_INTERVAL_MIN,
            run_scammer_warning_watch,
        )

        job_queue.run_repeating(
            _tick_wrapper("channel_scammer_warning", run_scammer_warning_watch, envelope=True),
            interval=SCAMMER_WARNING_INTERVAL_MIN * 60,
            first=540,
            name="channel_scammer_warning",
        )

    # Daily quality-content job (2026-08-29, quality overhaul deliverable 3):
    # up to 8 reranked quality posts/day on main, filling the 10-post budget
    # after the 2 market briefs. Budget-gated via main_cap (shared with every
    # main lane) so the day never exceeds 10. Dedup = stable-key + qwen3-4B
    # embed + the global _remember namespace — no double-post.
    if (
        os.getenv("RMI_DAILY_QUALITY_ENABLED", "true").lower() in ("1", "true", "yes")
        and _chat_id("main")
        and hasattr(job_queue, "run_repeating")
    ):
        from rugmunchbot.channels.scheduler._posts_daily import (
            DAILY_QUALITY_INTERVAL_MIN,
            post_daily_quality,
        )

        job_queue.run_repeating(
            _tick_wrapper("channel_daily_quality", post_daily_quality, envelope=True),
            interval=DAILY_QUALITY_INTERVAL_MIN * 60,
            first=420,
            name="channel_daily_quality",
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
            # envelope=True: successful ticks emit the channels:tick line with
            # items_posted — without it only FAILURES are visible in logs
            # (2026-08-25 fix; success was indistinguishable from silence).
            _tick_wrapper("channel_rag", post_rag_drafts, envelope=True),
            interval=int(os.getenv("RMI_RAG_POSTS_INTERVAL_MIN", "45")) * 60,
            first=900,
            name="channel_rag",
        )

    # Social-velocity spike monitor (win 36): narrative formation detection.
    # 2026-08-29 doctrine reroute: social velocity is ALPHA content
    # (data-driven market signal — CONTENT-STRATEGY-CANONICAL lists it under
    # ALPHA/WHALES), NOT a rug/exploit/scam detection. It posts to @rmialpha;
    # @rmialerts carries imminent-rug/exploit/scam detections ONLY.
    if ALPHA_CHANNEL_ENABLED and _chat_id("alpha") and hasattr(job_queue, "run_repeating"):

        async def _social_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                text = await build_social_spike_post()
                if text and _chat_id("alpha"):
                    from rugmunchbot.channels.scheduler._core import (
                        _post_photo_aware,
                        _safe_send,
                    )

                    await _post_photo_aware(
                        ctx.bot, _chat_id("alpha"), text, _safe_send, content_type="alpha"
                    )
            except Exception as exc:
                logger.warning("social_spike_tick failed: %s", str(exc)[:120])

        job_queue.run_repeating(
            _tick_wrapper("channel_social_spikes", _social_tick, envelope=True),
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
                    from rugmunchbot.channels.scheduler._core import (
                        _post_photo_aware,
                        _safe_send,
                    )

                    await _post_photo_aware(
                        ctx.bot, _chat_id("alerts"), text, _safe_send, content_type="alert"
                    )
            except Exception as exc:
                logger.warning("lp_expiry_tick failed: %s", str(exc)[:120])

        job_queue.run_repeating(
            _tick_wrapper("channel_lp_expiry", _lp_expiry_tick, envelope=True),
            interval=int(os.getenv("LP_EXPIRY_INTERVAL_H", "6")) * 3600,
            first=1800,
            name="channel_lp_expiry",
        )

    # Content rotation slots (win 32+): NOT registered — the per-type
    # generators were never wired, so these jobs fired daily and did nothing
    # except log noise (removed 2026-08-25). Re-register when a generator
    # actually exists; CONTENT_SLOTS stays in _content_rotation as the spec.

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
    # (news / main @cryptorugmuncher / alpha / alerts) — the post path routes
    # each scored story to exactly one destination via ``route_news``. The
    # alerts destination covers the scam-call lane (2026-08-29): scam-call
    # digests post to @rmialerts, so a deployment with only alerts+news
    # configured still sweeps and routes.
    if TG_NEWS_ENABLED and any(_chat_id(dest) for dest in ("news", "main", "alpha", "alerts")):
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

    if SCAM_ALERTS_PUBLISHER_ENABLED:
        # Shill/scam alerts from the RugBrain watchlist stream. Every finding
        # reaches the alerts page; risk >= RMI_SCAM_ALERTS_MAIN_RISK also
        # reaches the MAIN channel. Wrapped so it can never break the news path.
        job_queue.run_repeating(
            _tick_wrapper(
                "channel_scam_alerts_publisher", run_scam_alerts_publisher, envelope=True
            ),
            interval=SCAM_ALERTS_PUBLISHER_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["scam_alerts_publisher"],
            name="channel_scam_alerts_publisher",
        )
    if SECURITY_SEARCH_ENABLED:
        # Security keyword search (2026-09-02): ONE GetXAPI advanced_search
        # per tick against a rotating security query → RAG news_v2 (no bot
        # chat id needed — nothing is posted). 48 calls/day = $0.048/day.
        job_queue.run_repeating(
            _tick_wrapper("channel_security_search", run_security_search, envelope=True),
            interval=SECURITY_SEARCH_INTERVAL_MINUTES * 60,
            first=_FIRST_RUN_SECONDS["security_search"],
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
            first=_FIRST_RUN_SECONDS["social_monitor"],
            name="channel_social_trends",
        )
        job_queue.run_repeating(
            _tick_wrapper("channel_social_mentions", run_mentions, envelope=True),
            interval=SOCIAL_MONITOR_MENTIONS_INTERVAL_MIN * 60,
            first=_FIRST_RUN_SECONDS["social_monitor"] + 30,
            name="channel_social_mentions",
        )
        job_queue.run_repeating(
            _tick_wrapper("channel_social_status", run_account_status, envelope=True),
            interval=SOCIAL_MONITOR_STATUS_INTERVAL_HOURS * 3600,
            first=_FIRST_RUN_SECONDS["social_monitor"] + 60,
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
            first=_FIRST_RUN_SECONDS["scam_watch"] + 30,
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
            first=_FIRST_RUN_SECONDS["personal"],
            name="channel_personal",
        )
    else:
        logger.info(
            "channels:disabled",
            job="channel_personal",
            reason=("flag_off" if not PERSONAL_CHANNEL_ENABLED else "chat_id_unresolved"),
        )
    # The Morning Rug (branded 2026-08-31, formerly "Daily Market Summary") — 7:00 AM America/New_York, 7 days/week, on the
    # main channel (@cryptorugmuncher). Writes an editorial market breakdown
    # via ``chat_with_fallback(messages, "long")`` and posts it to the main
    # channel + the rugmunch.io feed (both fail-open) — see
    # ``channels/daily_market_summary.py``. Env knobs:
    # ``RMI_DAILY_MARKET_SUMMARY_ENABLED`` / ``_TIME`` / ``_TZ``.
    if DAILY_MARKET_SUMMARY_ENABLED and _chat_id("main"):
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

    if DAILY_RUG_ENABLED and _chat_id("main"):
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

    if DAILY_RUG_UPDATE_ENABLED and _chat_id("main"):
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

    if MIDDAY_REPORT_ENABLED and _chat_id("main"):
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

    if EVENING_WRAP_ENABLED and _chat_id("main"):
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
        logger.info("channels:disabled", job="channel_updates", reason="flag_off")
    # Channel roundup — RETIRED by default (2026-10-17, operator doctrine).
    # The standalone 3x-daily roundup duplicated the flagship "The Daily Rug
    # — Morning Market Brief" (channels/daily_rug), which already carries
    # "the day's best stories, ranked" every morning. Two link-list posts a
    # day read as filler. The module survives as the WEEKLY recap generator
    # (a 7-day arc the Daily Rug does not cover); re-enable with
    # RMI_ROUNDUP_INTERVAL_MIN > 0 (e.g. 10080 = weekly). See
    # ``scheduler/_posts_roundup.py`` for the fold decision.
    from rugmunchbot.channels.scheduler._posts_roundup import post_roundup

    roundup_min = int(os.getenv("RMI_ROUNDUP_INTERVAL_MIN", "0"))
    if roundup_min > 0 and _chat_id("main"):
        job_queue.run_repeating(
            _tick_wrapper("channel_roundup", post_roundup, envelope=True),
            interval=roundup_min * 60,
            first=_FIRST_RUN_SECONDS["news"] + 30,
            name="channel_roundup",
        )
    # Breaking-news fast lane (strategy v2): critical+fresh intel on a short
    # tick. 0 disables; shares dedupe with the slow lane so no double-post.
    from rugmunchbot.channels.scheduler._posts_rag import post_rag_fast_lane

    fast_lane_min = int(os.getenv("RMI_RAG_FAST_LANE_MIN", "10"))
    if fast_lane_min > 0 and ALERTS_CHANNEL_ENABLED and _chat_id("alerts"):
        job_queue.run_repeating(
            _tick_wrapper("channel_rag_fast", post_rag_fast_lane, envelope=True),
            interval=fast_lane_min * 60,
            first=20,
            name="channel_rag_fast",
        )
    # Security-tweet breaking-news escalation (2026-09-03): high-engagement
    # security tweets from monitored accounts post to main immediately — they
    # must not wait for the daily roundup. 0 disables; shares the global
    # dedupe so a tweet never double-posts.
    from rugmunchbot.channels.scheduler._posts_rag import (
        SECURITY_TWEET_ESCALATION_MIN,
        post_security_tweet_escalation,
    )

    if SECURITY_TWEET_ESCALATION_MIN > 0 and _chat_id("main"):
        job_queue.run_repeating(
            _tick_wrapper(
                "channel_security_tweet_escalation",
                post_security_tweet_escalation,
                envelope=True,
            ),
            interval=SECURITY_TWEET_ESCALATION_MIN * 60,
            first=30,
            name="channel_security_tweet_escalation",
        )
    logger.info(
        "channels:channel jobs registered (news/alerts/alpha/scans/tg_news/scam_watch/personal/channel_morning_rug/channel_midday_update/updates/shill_watch/kol_scoreboard/scammer_warning/daily_quality/ct_shill_watch/kol_daily_refresh)"
    )
