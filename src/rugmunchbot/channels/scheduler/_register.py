from __future__ import annotations

import os
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

    # Perps desk recap (2026-10-24): daily funding / OI / liquidation card with
    # the GMGN perps CTA. ALPHA content — a closed-ended data signal, which the
    # alpha charter allows ("show the signal, label the risk, link evidence");
    # never a rug/exploit detection. Card carries at most ONE link.
    if (
        ALPHA_CHANNEL_ENABLED
        and _chat_id("alpha")
        and hasattr(job_queue, "run_daily")
        and os.getenv("RMI_PERPS_RECAP_ENABLED", "true").lower() in ("1", "true", "yes")
    ):
        from datetime import UTC, time as _perps_time

        from rugmunchbot.channels.perps_recap import build_perps_recap_post

        async def _perps_recap_tick(ctx: ContextTypes.DEFAULT_TYPE) -> None:
            try:
                text = await build_perps_recap_post()
                if text and _chat_id("alpha"):
                    from rugmunchbot.channels.scheduler._core import (
                        _post_photo_aware,
                        _safe_send,
                    )

                    await _post_photo_aware(
                        ctx.bot, _chat_id("alpha"), text, _safe_send, content_type="alpha"
                    )
            except Exception as exc:
                logger.warning("perps_recap_tick failed: %s", str(exc)[:120])

        job_queue.run_daily(
            _tick_wrapper("channel_perps_recap", _perps_recap_tick, envelope=True),
            time=_perps_time(
                hour=int(os.getenv("RMI_PERPS_RECAP_HOUR_UTC", "7")), minute=0, tzinfo=UTC
            ),
            name="channel_perps_recap",
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
    from rugmunchbot.channels.scheduler._register_daily import register_daily_jobs
    from rugmunchbot.channels.scheduler._register_intel import register_intel_jobs
    from rugmunchbot.channels.scheduler._register_lanes import register_lane_jobs

    register_intel_jobs(job_queue)
    register_daily_jobs(job_queue)
    register_lane_jobs(job_queue)
    logger.info(
        "channels:channel jobs registered (news/alerts/alpha/scans/tg_news/scam_watch/personal/channel_morning_rug/channel_midday_update/updates/shill_watch/kol_scoreboard/scammer_warning/daily_quality/ct_shill_watch/kol_daily_refresh)"
    )
