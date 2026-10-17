"""Evening Wrap — shared pipeline helpers (split from _publish).

The mechanical, side-effect-free helpers the publish pipeline uses:
HTML cleanup, prompt-echo stripping, story/link counting, language-leak
detection, the image-carrying post wrapper, and the Telegram splitter.
Kept separate so ``_publish.py`` stays under the 200-line file cap.
"""

from __future__ import annotations

import re

from rugmunchbot.channels.evening_wrap._config import EVENING_WRAP_MAX_CHARS

#: Prompt-echo markers stripped from LLM output before the gate.
ECHO_MARKERS = (
    "AI DISCLOSURE:",
    "OUTPUT FORMAT",
    "MASTHEAD (",
    "MASTHEAD DONE",
    "NOW THE BODY",
    "cryptonews_rss",
    "rag_intel",
    "ranked_stories",
    "HARD RULES",
    "SECTION HEADERS",
    "## THE RUG TOOLKIT",
    "El informe",
    "The user just asked",
    "No further action needed",
)


def cleanup_html(text: str, *, max_chars: int = EVENING_WRAP_MAX_CHARS) -> str:
    """Normalize LLM output to safe Telegram HTML (shared cleaner)."""
    from rugmunchbot.channels.daily_market_summary._publish import cleanup_html as _clean

    return _clean(text, max_chars=max_chars)


def strip_echo(raw: str) -> str:
    """Truncate *raw* at the first prompt-echo marker (guarded for ##)."""
    from rugmunchbot.channels.daily_market_summary import _publish as _ms

    for marker in ECHO_MARKERS:
        raw = _ms._strip_prompt_marker(raw, marker, guarded=marker.startswith("## "))
    return raw


def has_min_stories(text: str, *, min_links: int = 3) -> bool:
    """True when the wrap carries at least ``min_links`` distinct http links."""
    links = set(re.findall(r"https?://[^\s)>\"']+", text))
    return len(links) >= min_links


def is_language_leak(text: str) -> bool:
    """True when >20% of words are common Spanish/French/German stopwords."""
    plain = re.sub(r"<[^>]+>", " ", text)
    words = re.findall(r"[A-Za-zÀ-ÿ']+", plain.lower())
    if not words:
        return False
    from rugmunchbot.channels.daily_rug._lang import NON_ENGLISH_WORDS

    non_english = sum(1 for w in words if w in NON_ENGLISH_WORDS)
    return (non_english / len(words)) > 0.20


class ImagePost(str):
    """A wrap part carrying an ``image_url`` — duck-types ``NewsPost``."""

    image_url: str = ""

    def __new__(cls, text: str, image_url: str) -> ImagePost:
        obj = super().__new__(cls, text)
        obj.image_url = image_url
        return obj


def split_for_telegram(wrap: str, max_chars: int = EVENING_WRAP_MAX_CHARS) -> list[str]:
    """Split an HTML post into Telegram-sized parts at line boundaries."""
    if len(wrap) <= max_chars:
        return [wrap]
    parts: list[str] = []
    remaining = wrap
    while len(remaining) > max_chars:
        cut = remaining[:max_chars]
        newline = cut.rfind("\n")
        if newline > max_chars * 0.5:
            cut = cut[:newline]
        cut = cut.rstrip()
        if cut.endswith("<"):
            cut = cut[:-1]
        parts.append(cut)
        remaining = remaining[len(cut) :].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


__all__ = [
    "ECHO_MARKERS",
    "ImagePost",
    "cleanup_html",
    "has_min_stories",
    "is_language_leak",
    "split_for_telegram",
    "strip_echo",
]
