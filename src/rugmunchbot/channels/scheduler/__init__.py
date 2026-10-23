"""Scheduled channel-content jobs (package).

Modularized from the former single-module ``scheduler.py`` (2026-08-21).
This ``__init__`` is a pure re-export shim: every public name and signature
is unchanged, and callers keep importing ``channels.scheduler`` exactly as
before.

Layout:
  * ``_core.py``       — retries, dedupe, safe send, scoring/partitioning
  * ``_tick.py``       — per-channel tick wrapper + persisted state loader
  * ``_posts_news.py`` — news + alerts channel posters
  * ``_posts_scan.py`` — scan + alpha channel posters
  * ``_register.py``   — APScheduler job registration
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.channels.scheduler._core import (
    _FIRST_RUN_SECONDS as _FIRST_RUN_SECONDS,
    _MAX_POSTED as _MAX_POSTED,
    _POST_RETRIES as _POST_RETRIES,
    _RETRY_BASE_DELAY as _RETRY_BASE_DELAY,
    _RETRY_MAX_DELAY as _RETRY_MAX_DELAY,
    _TG_NEWS_INTERVAL_MINUTES as _TG_NEWS_INTERVAL_MINUTES,
    _chat_id as _chat_id,
    _dedupe_key as _dedupe_key,
    _elapsed_ms as _elapsed_ms,
    _env_interval_minutes as _env_interval_minutes,
    _env_retry_count as _env_retry_count,
    _item_to_article as _item_to_article,
    _post_photo_aware as _post_photo_aware,
    _posted_keys as _posted_keys,
    _remember as _remember,
    _route_chat_id as _route_chat_id,
    _safe_send as _safe_send,
    _score_and_partition as _score_and_partition,
    logger as logger,
)
from rugmunchbot.channels.scheduler._posts_news import (
    post_alert_update as post_alert_update,
)
from rugmunchbot.channels.scheduler._posts_scan import (
    post_alpha_update as post_alpha_update,
    post_scan_update as post_scan_update,
)
from rugmunchbot.channels.scheduler._register import (
    register_channel_jobs as _register_channel_jobs,
)
from rugmunchbot.channels.scheduler._tick import (
    _load_channel_state as _load_channel_state,
    _tick_wrapper as _tick_wrapper,
)


def register_channel_jobs(app: Any) -> None:
    """Register channel jobs, then the unified content-automation lanes."""
    _register_channel_jobs(app)
    from rugmunchbot.channels.automation.register import register_content_jobs

    register_content_jobs(app)


__all__ = [
    "post_alert_update",
    "post_alpha_update",
    "post_scan_update",
    "register_channel_jobs",
]
