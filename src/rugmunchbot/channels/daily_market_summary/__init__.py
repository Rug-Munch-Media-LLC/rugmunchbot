"""Daily market summary channel post (package).

Modularized from the former single-module ``daily_market_summary.py``
(2026-08-21). This ``__init__`` is a pure re-export shim: every public name
and signature is unchanged, and callers keep importing
``channels.daily_market_summary`` exactly as before.

Layout:
  * ``_config.py``   — env switches, schedule window, char budget
  * ``_fmt.py``      — number/percent/volume formatting
  * ``_fetch.py``    — parallel data gathering (prices/metrics/movers/
                       narratives/KOL signals)
  * ``_assemble.py`` — summary-data orchestration + context serialization
  * ``_publish.py``  — LLM prompt, HTML cleanup, feed publish, posting
"""

from __future__ import annotations

from rugmunchbot.channels.daily_market_summary._assemble import (
    _serialize_context as _serialize_context,
    gather_summary_data as gather_summary_data,
)
from rugmunchbot.channels.daily_market_summary._config import (
    DAILY_MARKET_SUMMARY_ENABLED as DAILY_MARKET_SUMMARY_ENABLED,
    DAILY_MARKET_SUMMARY_FEED_ENABLED as DAILY_MARKET_SUMMARY_FEED_ENABLED,
    DAILY_MARKET_SUMMARY_MAX_CHARS as DAILY_MARKET_SUMMARY_MAX_CHARS,
    DAILY_MARKET_SUMMARY_TIME as DAILY_MARKET_SUMMARY_TIME,
    DAILY_MARKET_SUMMARY_TZ as DAILY_MARKET_SUMMARY_TZ,
    DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED as DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED,
    _env_bool_alias as _env_bool_alias,
    _env_int_alias as _env_int_alias,
    _env_str_alias as _env_str_alias,
    logger as logger,
    summary_run_time as summary_run_time,
)
from rugmunchbot.channels.daily_market_summary._fetch import (
    _SIGNAL_PREVIEW_MAX as _SIGNAL_PREVIEW_MAX,
    _SIGNAL_SNIPPET_MAX as _SIGNAL_SNIPPET_MAX,
    _TAG_RE as _TAG_RE,
    _WS_RE as _WS_RE,
    COINGECKO_API as COINGECKO_API,
    KOL_SIGNAL_QUERIES as KOL_SIGNAL_QUERIES,
    KOL_TOKEN_QUERIES_MAX as KOL_TOKEN_QUERIES_MAX,
    NARRATIVE_QUERIES as NARRATIVE_QUERIES,
    _fetch_global_metrics as _fetch_global_metrics,
    _fetch_kol_signals as _fetch_kol_signals,
    _fetch_major_prices as _fetch_major_prices,
    _fetch_market_condition as _fetch_market_condition,
    _fetch_movers as _fetch_movers,
    _fetch_narratives as _fetch_narratives,
    _fetch_news as _fetch_news,
    _fetch_trending as _fetch_trending,
    _html_preview as _html_preview,
    _kol_fetch_page as _kol_fetch_page,
    _kol_signal_queries as _kol_signal_queries,
)
from rugmunchbot.channels.daily_market_summary._fmt import (
    _fmt_pct as _fmt_pct,
    _fmt_price as _fmt_price,
    _fmt_volume as _fmt_volume,
)
from rugmunchbot.channels.daily_market_summary._publish import (
    _ALLOWED_TAG_RE as _ALLOWED_TAG_RE,
    _BOLD_RE as _BOLD_RE,
    _CODE_FENCE_RE as _CODE_FENCE_RE,
    _CODE_INLINE_RE as _CODE_INLINE_RE,
    _HEADER_RE as _HEADER_RE,
    _ITALIC_RE as _ITALIC_RE,
    _PLACEHOLDER as _PLACEHOLDER,
    SYSTEM_PROMPT as SYSTEM_PROMPT,
    build_messages as build_messages,
    cleanup_html as cleanup_html,
    post_daily_market_summary as post_daily_market_summary,
    publish_to_feed as publish_to_feed,
)

__all__ = [
    "COINGECKO_API",
    "DAILY_MARKET_SUMMARY_ENABLED",
    "DAILY_MARKET_SUMMARY_FEED_ENABLED",
    "DAILY_MARKET_SUMMARY_MAX_CHARS",
    "DAILY_MARKET_SUMMARY_TIME",
    "DAILY_MARKET_SUMMARY_TZ",
    "DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED",
    "KOL_SIGNAL_QUERIES",
    "KOL_TOKEN_QUERIES_MAX",
    "NARRATIVE_QUERIES",
    "SYSTEM_PROMPT",
    "build_messages",
    "cleanup_html",
    "gather_summary_data",
    "post_daily_market_summary",
    "publish_to_feed",
    "summary_run_time",
]
