"""Daily rug update channel (package) — midday rug/scam intelligence brief.

Layout:
  * ``_config.py``  — env switches, midday schedule, char budget
  * ``_fetch.py``   — RAG + scanner-alert + shill-campaign gathering
  * ``_publish.py`` — LLM prompt, quality gate, feed mirror, posting
"""

from __future__ import annotations

from rugmunchbot.channels.daily_rug_update._config import (
    DAILY_RUG_UPDATE_ENABLED as DAILY_RUG_UPDATE_ENABLED,
    DAILY_RUG_UPDATE_FEED_ENABLED as DAILY_RUG_UPDATE_FEED_ENABLED,
    DAILY_RUG_UPDATE_MAX_CHARS as DAILY_RUG_UPDATE_MAX_CHARS,
    DAILY_RUG_UPDATE_TIME as DAILY_RUG_UPDATE_TIME,
    DAILY_RUG_UPDATE_TZ as DAILY_RUG_UPDATE_TZ,
    logger as logger,
    rug_update_run_time as rug_update_run_time,
)
from rugmunchbot.channels.daily_rug_update._fetch import (
    _RAG_COLLECTIONS as _RAG_COLLECTIONS,
    _serialize_context as _serialize_context,
    gather_rug_update_data as gather_rug_update_data,
    pick_update_image as pick_update_image,
)
from rugmunchbot.channels.daily_rug_update._publish import (
    EDITION as EDITION,
    FEED_POST_URL_FMT as FEED_POST_URL_FMT,
    SYSTEM_PROMPT as SYSTEM_PROMPT,
    build_messages as build_messages,
    cleanup_html as cleanup_html,
    post_daily_rug_update as post_daily_rug_update,
    publish_to_feed as publish_to_feed,
    quality_gate as quality_gate,
)

__all__ = [
    "DAILY_RUG_UPDATE_ENABLED",
    "DAILY_RUG_UPDATE_FEED_ENABLED",
    "DAILY_RUG_UPDATE_MAX_CHARS",
    "DAILY_RUG_UPDATE_TIME",
    "DAILY_RUG_UPDATE_TZ",
    "EDITION",
    "FEED_POST_URL_FMT",
    "SYSTEM_PROMPT",
    "_RAG_COLLECTIONS",
    "_serialize_context",
    "build_messages",
    "cleanup_html",
    "gather_rug_update_data",
    "pick_update_image",
    "post_daily_rug_update",
    "publish_to_feed",
    "quality_gate",
    "rug_update_run_time",
]
