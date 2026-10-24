"""Repeating news lanes — one registration step, split from ``_register``.

Extracted verbatim out of ``register_channel_jobs``. These are ``run_repeating``
fast lanes rather than daily editions, which is why they are their own module:
each cadence is env-tunable (``0`` disables) and shares the global dedupe so a
story never double-posts across lanes.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._tick import _tick_wrapper
from rugmunchbot.config import ALERTS_CHANNEL_ENABLED


def register_lane_jobs(job_queue: Any) -> None:
    """Register the roundup / fast-lane / security-escalation rails."""

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
    if roundup_min > 0 and _core._chat_id("main"):
        job_queue.run_repeating(
            _tick_wrapper("channel_roundup", post_roundup, envelope=True),
            interval=roundup_min * 60,
            first=_core._FIRST_RUN_SECONDS["news"] + 30,
            name="channel_roundup",
        )
    # Breaking-news fast lane (strategy v2): critical+fresh intel on a short
    # tick. 0 disables; shares dedupe with the slow lane so no double-post.
    from rugmunchbot.channels.scheduler._posts_rag import post_rag_fast_lane

    fast_lane_min = int(os.getenv("RMI_RAG_FAST_LANE_MIN", "10"))
    if fast_lane_min > 0 and ALERTS_CHANNEL_ENABLED and _core._chat_id("alerts"):
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
