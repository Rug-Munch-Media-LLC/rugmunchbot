"""Content-lane jobs — split out of ``register_channel_jobs``.

The daily quality post and the RugBrain content-distribution lane are main
channel content, gated by their own opt-out knobs and by the presence of a
main chat id. Both dedupe through the shared ``_remember`` namespace.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._tick import _tick_wrapper

_TRUE = ("1", "true", "yes")


def _register_daily_quality(job_queue: Any) -> None:
    """Up to 8 reranked quality posts/day on main, filling the 10-post budget."""
    if os.getenv("RMI_DAILY_QUALITY_ENABLED", "true").lower() not in _TRUE:
        return
    if not _core._chat_id("main"):
        return
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


def _register_rag_drafts(job_queue: Any) -> None:
    """RAG-ranked stories → quality-written drafts routed per story."""
    if os.getenv("RMI_RAG_POSTS_ENABLED", "true").lower() not in _TRUE:
        return
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


def _register_lp_expiry(job_queue: Any) -> None:
    """LP-expiry monitor (win 35): warn before liquidity unlocks."""
    from rugmunchbot.channels.scheduler._posts_lp_expiry import build_lp_expiry_post

    async def _lp_expiry_tick(ctx: Any) -> None:
        try:
            text = await build_lp_expiry_post()
            if text and _core._chat_id("alerts"):
                from rugmunchbot.channels.scheduler._core import _post_photo_aware, _safe_send

                await _post_photo_aware(
                    ctx.bot, _core._chat_id("alerts"), text, _safe_send, content_type="alert"
                )
        except Exception as exc:
            _core.logger.warning("lp_expiry_tick failed: %s", str(exc)[:120])

    job_queue.run_repeating(
        _tick_wrapper("channel_lp_expiry", _lp_expiry_tick, envelope=True),
        interval=int(os.getenv("LP_EXPIRY_INTERVAL_H", "6")) * 3600,
        first=1800,
        name="channel_lp_expiry",
    )


def register_content_jobs(job_queue: Any, *, alerts_enabled: bool) -> None:
    """Register the main-content lanes when the job queue supports repeating."""
    if not hasattr(job_queue, "run_repeating"):
        return
    _register_daily_quality(job_queue)
    _register_rag_drafts(job_queue)
    if alerts_enabled and _core._chat_id("alerts"):
        _register_lp_expiry(job_queue)
