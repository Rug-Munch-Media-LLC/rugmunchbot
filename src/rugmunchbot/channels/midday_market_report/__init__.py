"""Midday Market Report (package) — the macro brief, second of three.

The SECOND of three daily reports. DISTINCT from the Morning Brief: it
covers BROADER markets (equities, DXY, gold, rates) and their effect on
crypto, plus macro (Fed, jobless claims, CPI). It is NOT a second
crypto-story list.

Layout:
  * ``_config.py``  — env switches, midday schedule, graphic, char budget
  * ``_fetch.py``   — macro intel + macro-filtered reranker + crypto tape
  * ``_prompt.py``  — the macro editorial prompt
  * ``_publish.py`` — pipeline: gather → write → gate → feed → post
"""

from __future__ import annotations

from rugmunchbot.channels.midday_market_report._config import (
    MIDDAY_REPORT_ENABLED as MIDDAY_REPORT_ENABLED,
    MIDDAY_REPORT_FEED_ENABLED as MIDDAY_REPORT_FEED_ENABLED,
    MIDDAY_REPORT_GRAPHIC as MIDDAY_REPORT_GRAPHIC,
    MIDDAY_REPORT_MAX_CHARS as MIDDAY_REPORT_MAX_CHARS,
    MIDDAY_REPORT_TIME as MIDDAY_REPORT_TIME,
    MIDDAY_REPORT_TZ as MIDDAY_REPORT_TZ,
    logger as logger,
    midday_report_run_time as midday_report_run_time,
)
from rugmunchbot.channels.midday_market_report._fetch import (
    _serialize_context as _serialize_context,
    gather_midday_report_data as gather_midday_report_data,
    pick_midday_image as pick_midday_image,
)
from rugmunchbot.channels.midday_market_report._prompt import (
    EDITION as EDITION,
    SYSTEM_PROMPT as SYSTEM_PROMPT,
    build_messages as build_messages,
)
from rugmunchbot.channels.midday_market_report._publish import (
    FEED_POST_URL_FMT as FEED_POST_URL_FMT,
    TASK_CLASS as TASK_CLASS,
    cleanup_html as cleanup_html,
    post_midday_report as post_midday_report,
)

__all__ = [
    "EDITION",
    "FEED_POST_URL_FMT",
    "MIDDAY_REPORT_ENABLED",
    "MIDDAY_REPORT_FEED_ENABLED",
    "MIDDAY_REPORT_GRAPHIC",
    "MIDDAY_REPORT_MAX_CHARS",
    "MIDDAY_REPORT_TIME",
    "MIDDAY_REPORT_TZ",
    "SYSTEM_PROMPT",
    "TASK_CLASS",
    "_serialize_context",
    "build_messages",
    "cleanup_html",
    "gather_midday_report_data",
    "midday_report_run_time",
    "pick_midday_image",
    "post_midday_report",
]
