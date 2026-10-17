"""The Daily Rug — Morning Market Brief (package).

The flagship ONCE-DAILY longform morning edition: the best stories from
the previous day plus a forward-looking "Things to look out for" section.

Layout:
  * ``_config.py``  — env switches, morning schedule, logo URL, char budget
  * ``_guard.py``   — once-per-day date guard (idempotent daily job)
  * ``_fetch.py``   — ranked reranker + RAG + scanner-alert gathering
  * ``_prompt.py``  — the longform editorial prompt
  * ``_lang.py``    — non-English stopword set for the language-leak gate
  * ``_publish.py`` — pipeline: gather → write → gate → feed → post
"""

from __future__ import annotations

from rugmunchbot.channels.daily_rug._config import (
    DAILY_RUG_ENABLED as DAILY_RUG_ENABLED,
    DAILY_RUG_FEED_ENABLED as DAILY_RUG_FEED_ENABLED,
    DAILY_RUG_LOGO_URL as DAILY_RUG_LOGO_URL,
    DAILY_RUG_MAX_CHARS as DAILY_RUG_MAX_CHARS,
    DAILY_RUG_TIME as DAILY_RUG_TIME,
    DAILY_RUG_TZ as DAILY_RUG_TZ,
    daily_rug_run_time as daily_rug_run_time,
    logger as logger,
)
from rugmunchbot.channels.daily_rug._fetch import (
    _serialize_context as _serialize_context,
    gather_daily_rug_data as gather_daily_rug_data,
    pick_daily_rug_image as pick_daily_rug_image,
)
from rugmunchbot.channels.daily_rug._guard import (
    already_posted_today as already_posted_today,
    claim_today as claim_today,
    release_today as release_today,
)
from rugmunchbot.channels.daily_rug._prompt import (
    EDITION as EDITION,
    SYSTEM_PROMPT as SYSTEM_PROMPT,
    build_messages as build_messages,
)
from rugmunchbot.channels.daily_rug._publish import (
    FEED_POST_URL_FMT as FEED_POST_URL_FMT,
    cleanup_html as cleanup_html,
    post_daily_rug as post_daily_rug,
)

__all__ = [
    "DAILY_RUG_ENABLED",
    "DAILY_RUG_FEED_ENABLED",
    "DAILY_RUG_LOGO_URL",
    "DAILY_RUG_MAX_CHARS",
    "DAILY_RUG_TIME",
    "DAILY_RUG_TZ",
    "EDITION",
    "FEED_POST_URL_FMT",
    "SYSTEM_PROMPT",
    "_serialize_context",
    "already_posted_today",
    "build_messages",
    "claim_today",
    "cleanup_html",
    "daily_rug_run_time",
    "gather_daily_rug_data",
    "pick_daily_rug_image",
    "post_daily_rug",
    "release_today",
]
