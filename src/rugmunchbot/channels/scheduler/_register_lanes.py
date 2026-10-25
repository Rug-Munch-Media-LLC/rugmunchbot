"""Repeating news lanes — one registration step, split from ``_register``.

These are ``run_repeating`` fast lanes rather than daily editions, which is why
they are their own module: each cadence is env-tunable (``0`` disables) and
shares the global dedupe so a story never double-posts across lanes.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._tick import _tick_wrapper
from rugmunchbot.config import ALERTS_CHANNEL_ENABLED


def _register_roundup(job_queue: Any) -> None:
    """Weekly recap generator — RETIRED by default (2026-10-17 doctrine).

    The 3x-daily roundup duplicated the flagship Daily Rug; the module survives
    as the weekly arc (set ``RMI_ROUNDUP_INTERVAL_MIN`` > 0, e.g. 10080).
    """
    from rugmunchbot.channels.scheduler._posts_roundup import post_roundup

    roundup_min = int(os.getenv("RMI_ROUNDUP_INTERVAL_MIN", "0"))
    if roundup_min > 0 and _core._chat_id("main"):
        job_queue.run_repeating(
            _tick_wrapper("channel_roundup", post_roundup, envelope=True),
            interval=roundup_min * 60,
            first=_core._FIRST_RUN_SECONDS["news"] + 30,
            name="channel_roundup",
        )


def _register_fast_lane(job_queue: Any) -> None:
    """Breaking-news fast lane: critical+fresh intel on a short tick."""
    from rugmunchbot.channels.scheduler._posts_rag import post_rag_fast_lane

    fast_lane_min = int(os.getenv("RMI_RAG_FAST_LANE_MIN", "10"))
    if fast_lane_min > 0 and ALERTS_CHANNEL_ENABLED and _core._chat_id("alerts"):
        job_queue.run_repeating(
            _tick_wrapper("channel_rag_fast", post_rag_fast_lane, envelope=True),
            interval=fast_lane_min * 60,
            first=20,
            name="channel_rag_fast",
        )


def _register_security_escalation(job_queue: Any) -> None:
    """High-engagement security tweets post to main immediately."""
    from rugmunchbot.channels.scheduler._posts_rag import (
        SECURITY_TWEET_ESCALATION_MIN,
        post_security_tweet_escalation,
    )

    if SECURITY_TWEET_ESCALATION_MIN > 0 and _core._chat_id("main"):
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


def register_lane_jobs(job_queue: Any) -> None:
    """Register the roundup / fast-lane / security-escalation rails."""
    _register_roundup(job_queue)
    _register_fast_lane(job_queue)
    _register_security_escalation(job_queue)
