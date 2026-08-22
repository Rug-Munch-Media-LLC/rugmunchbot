from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rugmunchbot.channels.content import (
    fetch_risk_alerts,
    fetch_trending_news,
)
from rugmunchbot.channels.formatters import (
    format_alerts_post,
    format_main_escalation,
    format_news_post,
)
from rugmunchbot.config import (
    ALERTS_CHANNEL_LIMIT,
    NEWS_CHANNEL_LIMIT,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

from typing import TYPE_CHECKING

from rugmunchbot.channels.scheduler._core import (  # noqa: F401
    _FIRST_RUN_SECONDS,
    _chat_id,
    _dedupe_key,
    _elapsed_ms,
    _post_photo_aware,
    _posted_keys,
    _remember,
    _safe_send,
    _score_and_partition,
    logger,
)
from rugmunchbot.channels.scheduler._tick import (  # noqa: F401
    _load_channel_state,
    _tick_wrapper,
)


async def post_news_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """DEPRECATED 2026-08-16 — legacy 4h news digest (job ``channel_news``).

    Superseded by the routed tg_news path (``post_tg_news_update`` +
    ``channels/router.py::route_news``). The ``channel_news`` registration in
    :func:`register_channel_jobs` is commented out so the news channel is no
    longer double-posted by two jobs with different dedupe prefixes (``news:``
    here vs ``tgnews:`` in the tg_news lane). Kept importable for
    ``channels/__init__.py`` and direct-call tests; remove in a follow-up.

    Posts the news digest to the news channel. Returns items sent (0-1).
    """
    started = time.perf_counter()
    chat_id = _chat_id("news")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_news",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    items = await fetch_trending_news(NEWS_CHANNEL_LIMIT)
    fresh = [item for item in items if _remember("news", _dedupe_key("news", item))]
    if not fresh:
        logger.info(
            "channels:tick",
            job="channel_news",
            items_swept=len(items),
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    ok = await _safe_send(ctx.bot, chat_id, format_news_post(fresh))
    if ok:
        logger.info("channels:news posted %s headline(s)", len(fresh))
    logger.info(
        "channels:tick",
        job="channel_news",
        items_swept=len(items),
        items_scored=len(fresh),
        items_posted=len(fresh) if ok else 0,
        items_failed=0 if ok else 1,
        latency_ms=_elapsed_ms(started),
    )
    return 1 if ok else 0


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
    alerts = await fetch_risk_alerts(ALERTS_CHANNEL_LIMIT)
    fresh = [item for item in alerts if _remember("alerts", _dedupe_key("alerts", item))]
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
    posted = 0
    if channel_items:
        ok = await _post_photo_aware(
            ctx.bot, chat_id, format_alerts_post(channel_items), _safe_send
        )
        if ok:
            posted += 1
    main_chat_id = _chat_id("main")
    if main_chat_id and main_items:
        for item in sorted(main_items, key=lambda i: i.get("importance", 0.0), reverse=True):
            if not _remember("alertsmain", _dedupe_key("alertsmain", item)):
                continue
            ok = await _post_photo_aware(
                ctx.bot, main_chat_id, format_main_escalation(item), _safe_send
            )
            if ok:
                posted += 1
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
