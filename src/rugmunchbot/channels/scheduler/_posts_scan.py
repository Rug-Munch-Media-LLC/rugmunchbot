from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rugmunchbot.channels._scan_commentary import attach_scan_commentary
from rugmunchbot.channels.content import (
    fetch_alpha_content,
    fetch_scan_notes,
)
from rugmunchbot.channels.formatters import (
    format_alpha_post,
    format_main_escalation,
    format_scan_card,
)
from rugmunchbot.channels.router import (
    ROUTE_ALPHA_CATEGORY,
)
from rugmunchbot.config import (
    ALPHA_CHANNEL_LIMIT,
    SCAN_CHANNEL_LIMIT,
    SCAN_CHANNEL_TICK_LIMIT,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

from typing import TYPE_CHECKING

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


async def post_scan_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the day's notable scans to @rmiscans, one compact card each.
    High-importance scans (≥0.7) ALSO escalate solo to @cryptorugmuncher.

    Returns the number of cards posted (0..``SCAN_CHANNEL_TICK_LIMIT``). The
    day's most notable scanned tokens (launch-alert ≥70 rail + scan history,
    ``channels/content.py::fetch_scan_notes``) are deduped per token
    (``_remember("scans", …)`` so the same token is never reposted), reranked
    (``channels/importance.py``), and every item at/above the news floor
    (``route_news``, default 0.4) posts as its own compact card — real risk
    reasons (honeypot / bundle / LP removed / flagged deployer) + risk score
    + token + chain + scan link (rugmunch.io/scanner), each card topped with
    a bounded witty one-liner (``attach_scan_commentary``, fail-open to no
    line). Items scoring ≥0.7 ALSO post solo to @cryptorugmuncher so the
    most notable token bangers reach the broader audience. Fail-open: an
    empty pool (no scans today / backend down) posts nothing, and a reranker
    outage keeps the channel flowing with the heuristic fallback (full
    window) exactly like the alerts job.
    """
    started = time.perf_counter()
    chat_id = _chat_id("scans")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_scans",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    scans = await fetch_scan_notes(SCAN_CHANNEL_LIMIT)
    fresh = [item for item in scans if _remember("scans", _dedupe_key("scans", item))]
    if not fresh:
        logger.info(
            "channels:tick",
            job="channel_scans",
            items_swept=len(scans),
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    main_items, keep, reranker_down = await _score_and_partition(
        fresh, category="news", source="scanner"
    )
    # Post at most SCAN_CHANNEL_TICK_LIMIT cards per tick: the reranker gate
    # (skip <0.4) + per-token dedupe are the volume controls; the tick cap
    # just spreads the day's notable scans across the day (~15-20/day at the
    # default 60m cadence).
    posted = 0
    for item in sorted(keep, key=lambda i: i.get("importance", 0.0), reverse=True)[
        :SCAN_CHANNEL_TICK_LIMIT
    ]:
        # One bounded witty one-liner per card (fail-open to no line).
        item = await attach_scan_commentary(item)
        ok = await _post_photo_aware(ctx.bot, chat_id, format_scan_card(item), _safe_send)
        if ok:
            posted += 1
    # Escalate high-importance scans (≥0.7) to the main channel @cryptorugmuncher
    # so the most notable token scans also reach the broader audience.
    main_chat_id = _chat_id("main")
    if main_chat_id and main_items:
        for item in sorted(main_items, key=lambda i: i.get("importance", 0.0), reverse=True):
            if not _remember("scansmain", _dedupe_key("scansmain", item)):
                continue
            # WO-2 hard cap: ≤9/day on main; overflow drops (the scans
            # card above already carries the item natively).
            if await _main_admission("scan") != "main":
                continue
            item = await attach_scan_commentary(item)
            ok = await _post_photo_aware(ctx.bot, main_chat_id, format_scan_card(item), _safe_send)
            if ok:
                posted += 1
                await _record_main_post()
    logger.info(
        "channels:tick",
        job="channel_scans",
        items_swept=len(scans),
        items_scored=len(fresh),
        items_posted=posted,
        items_failed=0,
        latency_ms=_elapsed_ms(started),
        reason=("reranker_fallback" if reranker_down else "below_floor" if not posted else None),
    )
    return posted


async def post_alpha_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Post the curated alpha digest (whale/smart/launch); escalate ≥0.7 to main.

    Returns the number of messages posted (0-1 native digest + 0..N main
    escalations). Whale/smart/launch items are deduped per section, reranked
    (``channels/importance.py``), and routed through ``route_news`` (category
    "alpha"): below the news floor they are dropped (rerank mode only), the
    rest form the alpha digest (verdict + links, photo when available), and
    items ≥0.7 ALSO post solo to @cryptorugmuncher. Fail-open: a reranker
    outage keeps the digest flowing (heuristic fallback) and never blocks
    the JobQueue.
    """
    started = time.perf_counter()
    chat_id = _chat_id("alpha")
    if not chat_id:
        logger.info(
            "channels:tick",
            job="channel_alpha",
            items_swept=0,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_chat_id",
        )
        return 0
    content = await fetch_alpha_content(
        chain="solana",
        whale_limit=max(1, ALPHA_CHANNEL_LIMIT),
        smart_limit=ALPHA_CHANNEL_LIMIT,
        launch_limit=ALPHA_CHANNEL_LIMIT,
    )
    whale_fresh = [
        m for m in content["whale_moves"] if _remember("alpha:whale", _dedupe_key("whale", m))
    ]
    smart_fresh = [
        w for w in content["smart_money"] if _remember("alpha:smart", _dedupe_key("smart", w))
    ]
    launch_fresh = [
        item
        for item in content["launches"]
        if _remember("alpha:launch", _dedupe_key("launch", item))
    ]
    swept = len(content["whale_moves"]) + len(content["smart_money"]) + len(content["launches"])
    scored = len(whale_fresh) + len(smart_fresh) + len(launch_fresh)
    if not scored:
        logger.info(
            "channels:tick",
            job="channel_alpha",
            items_swept=swept,
            items_scored=0,
            items_posted=0,
            items_failed=0,
            latency_ms=_elapsed_ms(started),
            reason="no_fresh",
        )
        return 0
    whale_main, whale_keep, whale_down = await _score_and_partition(
        whale_fresh, category=ROUTE_ALPHA_CATEGORY, source="WhaleWatcher"
    )
    smart_main, smart_keep, smart_down = await _score_and_partition(
        smart_fresh, category=ROUTE_ALPHA_CATEGORY, source="smart_money_feed"
    )
    launch_main, launch_keep, launch_down = await _score_and_partition(
        launch_fresh, category=ROUTE_ALPHA_CATEGORY, source="launch_feed"
    )
    reranker_down = whale_down or smart_down or launch_down
    posted = 0
    if whale_keep or smart_keep or launch_keep:
        ok = await _post_photo_aware(
            ctx.bot,
            chat_id,
            format_alpha_post(whale_keep, smart_keep, launch_keep),
            _safe_send,
        )
        if ok:
            posted += 1
    main_chat_id = _chat_id("main")
    if main_chat_id:
        escalated = sorted(
            [*whale_main, *smart_main, *launch_main],
            key=lambda i: i.get("importance", 0.0),
            reverse=True,
        )
        for item in escalated:
            if not _remember("alphamain", _dedupe_key("alphamain", item)):
                continue
            # WO-2 hard cap: ≤9/day on main; overflow drops (the alpha
            # digest above already carries the item natively).
            if await _main_admission("alpha") != "main":
                continue
            ok = await _post_photo_aware(
                ctx.bot, main_chat_id, format_main_escalation(item), _safe_send
            )
            if ok:
                posted += 1
                await _record_main_post()
    if posted:
        logger.info(
            "channels:alpha posted=%s whales=%s smart=%s launches=%s main=%s",
            posted,
            len(whale_keep),
            len(smart_keep),
            len(launch_keep),
            len(escalated) if main_chat_id else 0,
        )
    logger.info(
        "channels:tick",
        job="channel_alpha",
        items_swept=swept,
        items_scored=scored,
        items_posted=posted,
        items_failed=0,
        latency_ms=_elapsed_ms(started),
        reason=("reranker_fallback" if reranker_down else "below_floor" if not posted else None),
    )
    return posted
