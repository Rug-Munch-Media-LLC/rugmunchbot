from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rugmunchbot.channels.content import (
    fetch_risk_alerts,
)
from rugmunchbot.channels.formatters import (
    format_alerts_post,
    format_main_escalation,
)
from rugmunchbot.config import (
    ALERTS_CHANNEL_LIMIT,
)
from rugmunchbot.support.image_policy import ensure_post_image

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

from rugmunchbot.channels.scheduler._core import (  # noqa: F401
    _FIRST_RUN_SECONDS,
    _chat_id,
    _dedupe_key,
    _elapsed_ms,
    _main_admission,
    _post_photo_aware,
    _posted_keys,
    _record_main_post,
    _remember,
    _safe_send,
    _score_and_partition,
    logger,
)
from rugmunchbot.channels.scheduler._tick import (  # noqa: F401
    _load_channel_state,
    _tick_wrapper,
)


def _index_alerts_to_rag(alerts: list[dict[str, Any]]) -> int:
    """W8 (CONTENT-STRATEGY-CANONICAL v2): fire-and-forget index fresh
    alerts into the RAG scan-memory so chat can cite live alert intel.

    Alert items already match the ``scan_v1`` summary shape (address,
    chain, risk_score, holders, liquidity); only the flag key differs
    (``risk_flags`` vs ``threats``) and is remapped on a shallow copy.
    Fail-open: indexing never blocks or breaks posting. Returns the
    number of items handed to the background indexer.
    """
    try:
        from rugmunchbot.tools.scanner import schedule_scan_memory_index

        indexed = 0
        for item in alerts:
            if not item.get("address"):
                continue
            payload = dict(item)
            payload.setdefault("threats", item.get("risk_flags") or [])
            if schedule_scan_memory_index(payload, source="alert") is not None:
                indexed += 1
        if indexed:
            logger.info("alerts_indexed_to_rag count=%d", indexed)
        return indexed
    except Exception as e:
        logger.debug("alerts_rag_index_skipped err=%s", str(e)[:120])
        return 0


async def post_alert_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the curated scanner risk digest; escalate ≥0.7 items to the main channel.

    Returns the number of messages posted (0-1 native digest + 0..N main
    escalations). Every fresh alert is reranked (``channels/importance.py``)
    and routed through ``route_news`` (category "news"): below the news floor
    it is dropped, 0.4-0.7 it posts on the alerts channel (rich card: alert
    type, condition met vs current, why-it-matters, scan/chart links), and
    ≥0.7 it ALSO posts solo to @cryptorugmuncher. Fail-open: a reranker
    outage keeps the digest flowing (heuristic fallback) and never blocks
    the JobQueue.
    """
    started = time.perf_counter()
    chat_id = _chat_id("alerts")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_alerts",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    # Channel doctrine (2026-09-03): @rmialerts is security-content ONLY —
    # scanner risk alerts, never whale moves, scans, or system notifications.
    # This file is the ALERTS poster (despite the ``_posts_news`` name); the
    # @rmicryptonews breaking-news lane lives in ``telegram_news``. Defense-
    # in-depth on top of the ``_safe_send`` chokepoint: if the alerts channel
    # is ever misconfigured to accept a non-alert content type, refuse to
    # post rather than leak scanner/whale/system noise into it.
    from rugmunchbot.channels.style_guides import channel_accepts_content

    if not channel_accepts_content("alerts", "alert"):
        logger.warning("channels:alerts_contract_blocked")
        return 0
    alerts = await fetch_risk_alerts(ALERTS_CHANNEL_LIMIT)
    fresh = [
        item
        for item in alerts
        if _remember("alerts", _dedupe_key("alerts", item), title=item.get("title"))
    ]
    if not fresh:
        logger.info(
            "channels:tick",
            job="channel_alerts",
            items_swept=len(alerts),
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    main_items, channel_items, reranker_down = await _score_and_partition(
        fresh, category="news", source="scanner"
    )
    _index_alerts_to_rag(fresh)
    posted = 0
    if channel_items:
        ok = await _post_photo_aware(
            ctx.bot,
            chat_id,
            ensure_post_image(format_alerts_post(channel_items)),
            _safe_send,
            content_type="alert",
        )
        if ok:
            posted += 1
    main_chat_id = _chat_id("main")
    if main_chat_id and main_items:
        for item in sorted(main_items, key=lambda i: i.get("importance", 0.0), reverse=True):
            if not _remember(
                "alertsmain", _dedupe_key("alertsmain", item), title=item.get("title")
            ):
                continue
            # WO-2 hard cap: ≤9/day on main; overflow drops (the alerts
            # digest above already carries the item natively).
            if await _main_admission("alert") != "main":
                continue
            # STORY-FIRST doctrine: main gets the founder-voice narrative,
            # not a raw alert card. AI story generated async; falls back to
            # the standard escalation card on any failure.
            from rugmunchbot.channels._alert_story import write_alert_story
            from rugmunchbot.channels.formatters._primitives import (
                enforce_main_link_contract,
            )

            story = await write_alert_story(item)
            if story is not None:
                main_text = enforce_main_link_contract(story, item)
            else:
                main_text = enforce_main_link_contract(format_main_escalation(item), item)
            if main_text is None:
                logger.warning(
                    "main_link_contract_blocked type=alert name=%s", str(item.get("name"))[:40]
                )
                continue
            ok = await _post_photo_aware(
                ctx.bot,
                main_chat_id,
                ensure_post_image(main_text),
                _safe_send,
                content_type="alert",
            )
            if ok:
                posted += 1
                await _record_main_post()
                # W30 top-of-food-chain mirror: every main post also lands on
                # feed.rugmunch.io (scanner risk-alert escalations ≥0.7).
                try:
                    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed

                    mirror_post_to_feed(
                        {
                            "text": str(main_text),
                            "story_ref": str(
                                item.get("url")
                                or item.get("address")
                                or item.get("name")
                                or item.get("summary")
                                or ""
                            ),
                            "source": "scanner",
                            "kind": "alert",
                        }
                    )
                except Exception as exc:
                    logger.warning("alert_feed_mirror_failed err=%s", str(exc)[:120])
    if posted:
        logger.info(
            "channels:alerts posted=%s main=%s",
            posted,
            len(main_items),
        )
    logger.info(
        "channels:tick",
        job="channel_alerts",
        items_swept=len(alerts),
        items_scored=len(fresh),
        items_posted=posted,
        items_failed=0,
        latency_ms=_elapsed_ms(started),
        reason=("reranker_fallback" if reranker_down else "below_floor" if not posted else None),
    )
    return posted
