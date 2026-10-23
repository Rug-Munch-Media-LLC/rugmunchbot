"""Market category routing label: Gamma event tags first, keywords second.

The label decides which content lane a card belongs to. It is a routing hint,
not a claim about the market: when neither tags nor keywords resolve, the label
is ``other``.
"""

from __future__ import annotations

import re
from typing import Any

KNOWN_CATEGORIES = frozenset({"crypto", "macro", "politics", "tech", "sports"})

_CATEGORY_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "crypto",
        (
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "solana",
            "sol",
            "xrp",
            "doge",
            "crypto",
            "stablecoin",
            "defi",
            "etf",
            "coinbase",
            "binance",
            "token",
        ),
    ),
    (
        "sports",
        (
            "nba",
            "nfl",
            "nhl",
            "mlb",
            "lol",
            "cs2",
            "ufc",
            "cup",
            "league",
            "match",
            "finals",
            "vs",
            "gaming",
            "worlds",
        ),
    ),
    (
        "macro",
        (
            "fed",
            "fomc",
            "rate",
            "rates",
            "inflation",
            "cpi",
            "gdp",
            "recession",
            "treasury",
            "tariff",
            "tariffs",
            "unemployment",
        ),
    ),
    (
        "politics",
        (
            "election",
            "senate",
            "house",
            "president",
            "congress",
            "trump",
            "biden",
            "vote",
            "nato",
            "russia",
            "ukraine",
            "israel",
            "government",
            "parliament",
            "minister",
        ),
    ),
    (
        "tech",
        ("ai", "openai", "google", "apple", "nvidia", "spacex", "tesla", "launch", "model", "gpt"),
    ),
)

_PATTERNS = tuple(
    (name, re.compile(r"\b(?:" + "|".join(re.escape(word) for word in words) + r")\b"))
    for name, words in _CATEGORY_WORDS
)


def _from_tags(market: dict[str, Any]) -> str | None:
    events = market.get("events")
    if not isinstance(events, list):
        return None
    for event in events:
        tags = event.get("tags") if isinstance(event, dict) else None
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            label = str(tag.get("label") or tag.get("slug") or "").strip().lower()
            if label in KNOWN_CATEGORIES:
                return label
    return None


def classify_market(market: dict[str, Any]) -> str:
    """Return one of ``KNOWN_CATEGORIES`` or ``other``."""
    tagged = _from_tags(market)
    if tagged:
        return tagged
    text = f"{market.get('question') or ''} {market.get('slug') or ''}".lower()
    for name, pattern in _PATTERNS:
        if pattern.search(text):
            return name
    return "other"
