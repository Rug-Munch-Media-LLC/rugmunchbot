"""Display-safe token names/symbols for trending cards.

Pure string cleanup — no network I/O. Split out of
``handlers/_market_helpers.py`` (2026-10-23).
"""

from __future__ import annotations

import re
from typing import Any

# Garbage name patterns seen in DexScreener/CoinGecko/RugCharts payloads
# (meme descriptions, marketing sentences). Compiled once at import.
_GARBAGE_PATTERNS = tuple(
    re.compile(pattern, flags=re.IGNORECASE)
    for pattern in (
        r"on robinhood.*",
        r"crawled out of.*",
        r"just a smol.*",
        r"dev leave.*",
        r"community took over.*",
        r"sent to a.*",
        r"smolsem.*",
        r"anaem.*",
        r"the true origin.*",
        r"built her empire.*",
        r"the soft power.*",
        r"born in \d{4}.*",
        r"slightly sassy.*",
    )
)

_MAX_NAME_CHARS = 40
_MAX_NAME_WORDS = 5


def _clean_token_name(name: str, symbol: str) -> tuple[str, str]:
    """Clean up token names from DexScreener/CoinGecko/RugCharts."""
    if not name or name == "Unknown":
        return symbol or "Unknown", symbol or "???"

    # Filter out garbage patterns (meme descriptions, etc.)
    cleaned = name
    for pattern in _GARBAGE_PATTERNS:
        cleaned = pattern.sub("", cleaned).strip()

    # If name is too long or looks like a sentence, use symbol
    if len(cleaned) > _MAX_NAME_CHARS or cleaned.count(" ") > _MAX_NAME_WORDS:
        return symbol or "Unknown", symbol or "???"

    # If name is empty after cleaning, use symbol
    if not cleaned:
        return symbol or "Unknown", symbol or "???"

    # If symbol is in name, prefer name
    if symbol and symbol.upper() in cleaned.upper():
        return cleaned[:_MAX_NAME_CHARS], symbol

    return cleaned[:_MAX_NAME_CHARS], symbol or "???"


def _trending_display(token: dict[str, Any]) -> tuple[str, str]:
    """Return a display-safe (name, symbol), trimming long/garbage values."""
    raw_name = (token.get("name") or "").strip()
    symbol = (token.get("symbol") or "").strip()
    # If name looks like an address (starts with 0x or is 44 chars), use symbol
    if raw_name.startswith("0x") or len(raw_name) == 44:
        raw_name = ""
    return _clean_token_name(raw_name, symbol)
