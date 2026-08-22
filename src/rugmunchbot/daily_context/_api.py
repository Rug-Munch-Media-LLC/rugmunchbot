"""Daily market awareness public API — LLM context + /start market section.

Extracted verbatim from the former single-module ``daily_context.py``.
"""

from __future__ import annotations

from rugmunchbot.daily_context._cache import _load_daily_data
from rugmunchbot.daily_context._fmt import (
    _has_content,
    format_daily_context,
    format_market_section,
)


async def get_daily_context() -> str:
    """LLM-facing daily market context (Redis-cached 5 min). Fail-open -> ''."""
    data = await _load_daily_data()
    if not _has_content(data):
        return ""
    return format_daily_context(data)


async def get_market_section() -> str:
    """Compact 'Today's Market' section for /start. Fail-open -> ''."""
    data = await _load_daily_data()
    if not _has_content(data):
        return ""
    return format_market_section(data)
