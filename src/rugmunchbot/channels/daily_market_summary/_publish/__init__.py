"""Daily market summary publishing — split into focused sub-modules.

- ``format``   — edition naming, writer prompt, message building, HTML
                 cleanup, Telegram splitting
- ``fetch``    — report image resolution
- ``assemble`` — prompt-echo stripping and pre-ship guards
- ``send``     — Telegram delivery + rugmunch.io feed publish
"""

from __future__ import annotations

from rugmunchbot.channels.daily_market_summary._publish.assemble import (
    _LANGUAGE_LEAK_THRESHOLD as _LANGUAGE_LEAK_THRESHOLD,
    _NON_ENGLISH_WORDS as _NON_ENGLISH_WORDS,
    _SPEC_ECHO_HINTS as _SPEC_ECHO_HINTS,
    _has_min_stories as _has_min_stories,
    _is_language_leak as _is_language_leak,
    _strip_prompt_marker as _strip_prompt_marker,
)
from rugmunchbot.channels.daily_market_summary._publish.fetch import (
    _pick_report_image as _pick_report_image,
)
from rugmunchbot.channels.daily_market_summary._publish.format import (
    _ALLOWED_TAG_RE as _ALLOWED_TAG_RE,
    _BOLD_RE as _BOLD_RE,
    _CODE_FENCE_RE as _CODE_FENCE_RE,
    _CODE_INLINE_RE as _CODE_INLINE_RE,
    _HEADER_RE as _HEADER_RE,
    _ITALIC_RE as _ITALIC_RE,
    _PLACEHOLDER as _PLACEHOLDER,
    MIDDAY_EDITION as MIDDAY_EDITION,
    MORNING_EDITION as MORNING_EDITION,
    SYSTEM_PROMPT as SYSTEM_PROMPT,
    _edition_name as _edition_name,
    _edition_slug as _edition_slug,
    _resolve_edition as _resolve_edition,
    _split_for_telegram as _split_for_telegram,
    build_messages as build_messages,
    cleanup_html as cleanup_html,
)
from rugmunchbot.channels.daily_market_summary._publish.send import (
    _ImagePost as _ImagePost,
    post_daily_market_summary as post_daily_market_summary,
    publish_to_feed as publish_to_feed,
)

__all__ = [
    "MIDDAY_EDITION",
    "MORNING_EDITION",
    "SYSTEM_PROMPT",
    "build_messages",
    "cleanup_html",
    "post_daily_market_summary",
    "publish_to_feed",
]
