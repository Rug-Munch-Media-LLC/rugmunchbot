#!/usr/bin/env python3
"""
RugMunchBot — LIVE daily market awareness (package).

Modularized from the former single-module ``daily_context.py`` (2026-08-22).
This ``__init__`` is a pure re-export shim: every public name and signature is
unchanged, and callers keep importing ``rugmunchbot.daily_context`` exactly as
before.

Fetches a compact snapshot of *today's* crypto market from the backend and
injects it into bot responses:

  * Market overview  — BTC/ETH price, Fear & Greed, total mcap, dominance
                       (``/api/v1/market/overview`` + ``/api/v1/market/macro``)
  * Top news         — today's top stories (``/api/v1/news/trending``)
  * Trending tokens  — names + 24h change (``/api/v1/market/trending``)
  * Recent launches  — just-deployed tokens (``/api/v1/launches/recent``)

The assembled data dict is cached in Redis for 5 minutes so chat /start
paths never hammer the backend more than once per cache window. Every
fetch and cache operation is fail-open: any error degrades to an empty
section instead of breaking chat.

Stable-prefix contract:
  The digest CONTENT is volatile — it re-renders every ``CACHE_TTL`` (5 min)
  with live prices/news/trending/launches. Timestamps are DAY-GRANULARITY
  only (``%b %d %Y`` / ``%b %d`` — no HH:MM), so the *date prefix* is stable
  for the whole day, but the digest text itself is not. Consumers that build
  an LLM prompt MUST inject the digest AFTER the stable system-prompt prefix
  (e.g. appended to the user turn) — never inside the byte-stable prefix —
  otherwise the 5-minute re-render changes the shared prefix on every window.
  (Verified 2026-08-15: timestamps in both formatters are day-granularity;
  the volatile part is the data, not the date stamp.)

Public API
----------
``get_daily_context()`` -> str
    LLM-facing context block prepended to the backend chat prompt.
    Returns ``""`` when no data is available (fail-open).

``get_market_section()`` -> str
    Compact user-facing "Today's Market" section for the /start card.
    Returns ``""`` when no data is available.

``fetch_daily_data()`` -> dict
    Uncached parallel fetch of all sections (used by tests / reuse).

``format_daily_context(data)`` / ``format_market_section(data)``
    Pure formatters (unit-testable without network).

Layout:
  * ``_constants.py`` — endpoints, budgets, cache key/TTL
  * ``_fmt.py``       — pure formatters (labels, context block, market section)
  * ``_fetch.py``     — per-endpoint fail-open fetchers + parallel assembly
  * ``_cache.py``     — Redis snapshot cache (fail-open)
  * ``_api.py``       — get_daily_context / get_market_section
"""

from __future__ import annotations

from rugmunchbot.daily_context._api import (
    get_daily_context as get_daily_context,
    get_market_section as get_market_section,
)
from rugmunchbot.daily_context._cache import (
    _cache_get as _cache_get,
    _cache_set as _cache_set,
    _load_daily_data as _load_daily_data,
)
from rugmunchbot.daily_context._constants import (
    CACHE_KEY as CACHE_KEY,
    CACHE_TTL as CACHE_TTL,
    CONTEXT_MAX_CHARS as CONTEXT_MAX_CHARS,
)
from rugmunchbot.daily_context._fetch import (
    fetch_daily_data as fetch_daily_data,
)
from rugmunchbot.daily_context._fmt import (
    _fmt_mcap as _fmt_mcap,
    _fmt_price as _fmt_price,
    _has_content as _has_content,
    _launch_label as _launch_label,
    _trending_label as _trending_label,
    format_daily_context as format_daily_context,
    format_market_section as format_market_section,
)

__all__ = [
    "CACHE_KEY",
    "CACHE_TTL",
    "CONTEXT_MAX_CHARS",
    "fetch_daily_data",
    "format_daily_context",
    "format_market_section",
    "get_daily_context",
    "get_market_section",
]
