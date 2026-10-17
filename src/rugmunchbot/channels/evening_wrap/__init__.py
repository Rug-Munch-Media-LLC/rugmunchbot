"""Evening Wrap (package) — the day's recap, third of three.

The THIRD of three daily reports. DISTINCT from both the Morning Brief and
the Midday Market Report: it is the day's RECAP of the crypto underbelly —
the scams that ran, the KOLs shilling, what rug pulled. It is NOT a market
brief and NOT a macro read.

Layout:
  * ``_config.py``  — env switches, evening schedule, graphic, char budget
  * ``_fetch.py``   — scanner alerts + shill campaigns + recap intel
  * ``_prompt.py``  — the recap editorial prompt
  * ``_publish.py`` — pipeline: gather → write → gate → feed → post
"""

from __future__ import annotations

from rugmunchbot.channels.evening_wrap._config import (
    EVENING_WRAP_ENABLED as EVENING_WRAP_ENABLED,
    EVENING_WRAP_FEED_ENABLED as EVENING_WRAP_FEED_ENABLED,
    EVENING_WRAP_GRAPHIC as EVENING_WRAP_GRAPHIC,
    EVENING_WRAP_MAX_CHARS as EVENING_WRAP_MAX_CHARS,
    EVENING_WRAP_TIME as EVENING_WRAP_TIME,
    EVENING_WRAP_TZ as EVENING_WRAP_TZ,
    evening_wrap_run_time as evening_wrap_run_time,
    logger as logger,
)
from rugmunchbot.channels.evening_wrap._fetch import (
    _serialize_context as _serialize_context,
    gather_evening_wrap_data as gather_evening_wrap_data,
    pick_evening_image as pick_evening_image,
)
from rugmunchbot.channels.evening_wrap._prompt import (
    EDITION as EDITION,
    SYSTEM_PROMPT as SYSTEM_PROMPT,
    build_messages as build_messages,
)
from rugmunchbot.channels.evening_wrap._publish import (
    FEED_POST_URL_FMT as FEED_POST_URL_FMT,
    TASK_CLASS as TASK_CLASS,
    cleanup_html as cleanup_html,
    post_evening_wrap as post_evening_wrap,
)

__all__ = [
    "EDITION",
    "EVENING_WRAP_ENABLED",
    "EVENING_WRAP_FEED_ENABLED",
    "EVENING_WRAP_GRAPHIC",
    "EVENING_WRAP_MAX_CHARS",
    "EVENING_WRAP_TIME",
    "EVENING_WRAP_TZ",
    "FEED_POST_URL_FMT",
    "SYSTEM_PROMPT",
    "TASK_CLASS",
    "_serialize_context",
    "build_messages",
    "cleanup_html",
    "evening_wrap_run_time",
    "gather_evening_wrap_data",
    "pick_evening_image",
    "post_evening_wrap",
]
