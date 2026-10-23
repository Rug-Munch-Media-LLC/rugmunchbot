"""Canonical story CTA — every outbound content post drives to the full story.

Contract (2026-10-24): every content lane appends ONE teaser CTA row that
opens the story on rugmunch.io::

    Read the full story -> https://rugmunch.io/story/<slug>

The slug ALWAYS comes from the content payload (``story_slug`` / ``slug`` /
``ghost_slug``). A slug is never synthesised from a headline: an invented
slug 404s and attributes a story to a page that does not exist. When the
payload carries no usable slug the CTA degrades to the lane's section page
and the decision is recorded as ``story_url=None`` + ``reason`` in
:func:`story_cta` (logged by :func:`attach_story_cta`), never invented.

Attribution: every generated URL carries ``utm_source=telegram``,
``utm_medium=bot``, ``utm_campaign=<lane>``, ``utm_content=rugmunchbot`` and
``ref=rugmunchbot`` so the site can attribute Telegram traffic. The trading
referral strip (``referrals.referral_line``) is untouched — this row is
content attribution.

Pure module: no network, no DB, no Telegram import at module scope.
"""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.story_cta")

__all__ = [
    "CTA_MARKER",
    "SECTION_PAGES",
    "STORY_URL_FMT",
    "attach_story_cta",
    "attributed_url",
    "cta_line",
    "cta_markup",
    "story_cta",
    "story_slug",
]

#: Canonical story route on the main domain (``/story/<slug>`` is live).
STORY_URL_FMT: str = "https://rugmunch.io/story/{slug}"

#: Section page per lane — verified against https://rugmunch.io/sitemap.xml
#: (2026-10-24): /news, /feed, /market-intel, /scam-school, /wall-of-shame.
SECTION_PAGES: dict[str, str] = {
    "morning": "https://rugmunch.io/feed",
    "midday": "https://rugmunch.io/news",
    "evening": "https://rugmunch.io/feed",
    "daily": "https://rugmunch.io/feed",
    "news": "https://rugmunch.io/news",
    "scam": "https://rugmunch.io/scam-school",
    "shill": "https://rugmunch.io/wall-of-shame",
    "smart_money": "https://rugmunch.io/market-intel",
    "whales": "https://rugmunch.io/market-intel",
    "predictions": "https://rugmunch.io/market-intel",
}

#: Text marker making :func:`attach_story_cta` idempotent.
CTA_MARKER: str = "Read the full story"

#: Payload keys carrying a slug, in priority order.
_SLUG_KEYS: tuple[str, ...] = ("story_slug", "slug", "ghost_slug")

#: A real slug we are willing to put in a URL (never a headline).
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_MAX_SLUG = 96


def _clean_slug(value: Any) -> str:
    """Return a URL-safe slug, or ``""`` when *value* is not one. Pure."""
    raw = str(value or "").strip().lower()
    if not raw or len(raw) > _MAX_SLUG or not _SLUG_RE.fullmatch(raw):
        return ""
    return raw


def story_slug(payload: dict[str, Any] | None) -> str:
    """First usable slug in *payload* (``""`` when the payload has none)."""
    if not isinstance(payload, dict):
        return ""
    for key in _SLUG_KEYS:
        slug = _clean_slug(payload.get(key))
        if slug:
            return slug
    return ""


def attributed_url(url: str, lane: str) -> str:
    """Append UTM tags + ``ref`` to a rugmunch.io URL (idempotent, pure).

    Non-http(s) input is returned unchanged; existing query params survive.
    """
    if not url.startswith(("http://", "https://")):
        return url
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.setdefault("utm_source", "telegram")
    query.setdefault("utm_medium", "bot")
    query.setdefault("utm_campaign", lane or "content")
    query.setdefault("utm_content", "rugmunchbot")
    query.setdefault("ref", "rugmunchbot")
    return urlunsplit(parts._replace(query=urlencode(query)))


def story_cta(payload: dict[str, Any] | None, lane: str) -> dict[str, Any]:
    """Resolve the story CTA for one post. Pure — never raises.

    Returns ``{"lane", "story_url", "reason", "url"}`` where ``story_url`` is
    ``None`` (with a ``reason``: ``"slug_missing"`` / ``"slug_invalid"`` /
    ``"unknown_lane"``) whenever the payload has no real slug, and ``url`` is
    always a clickable destination (story → section page fallback).
    """
    fallback = SECTION_PAGES.get(lane, "")
    slug = story_slug(payload)
    if slug:
        return {
            "lane": lane,
            "story_url": attributed_url(STORY_URL_FMT.format(slug=slug), lane),
            "reason": None,
            "url": attributed_url(STORY_URL_FMT.format(slug=slug), lane),
        }
    raw_present = isinstance(payload, dict) and any(payload.get(k) for k in _SLUG_KEYS)
    if not fallback:
        reason = "unknown_lane"
    elif raw_present:
        reason = "slug_invalid"
    else:
        reason = "slug_missing"
    return {
        "lane": lane,
        "story_url": None,
        "reason": reason,
        "url": attributed_url(fallback, lane),
    }


def cta_line(payload: dict[str, Any] | None, lane: str) -> str:
    """The CTA row as Telegram-HTML: teaser link to story (or section page)."""
    resolved = story_cta(payload, lane)
    href = html.escape(resolved["url"], quote=True)
    label = CTA_MARKER if resolved["story_url"] else "More on this story"
    return f'\U0001f4d6 <a href="{href}">{label} \u2192</a>'


def attach_story_cta(text: str, payload: dict[str, Any] | None, lane: str) -> str:
    """Append the CTA row to *text* once (idempotent, fail-open). Pure.

    Logs the ``story_url:null`` decision (with reason) at DEBUG so a lane
    shipping a section-page fallback is visible in telemetry.
    """
    if not text or not text.strip():
        return text
    if CTA_MARKER in text or "rugmunch.io/story/" in text:
        return text
    resolved = story_cta(payload, lane)
    if not resolved["url"]:
        return text
    if resolved["story_url"] is None:
        logger.debug(
            "story_cta:fallback lane=%s reason=%s url=%s",
            lane,
            resolved["reason"],
            resolved["url"],
        )
    return f"{text.rstrip()}\n\n{cta_line(payload, lane)}"


def cta_markup(payload: dict[str, Any] | None, lane: str) -> Any:
    """Single-button inline keyboard pointing at the CTA (``None`` if unavailable).

    Only lanes whose send path accepts ``reply_markup`` use this; everything
    else ships :func:`cta_line` as a text row.
    """
    try:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    except Exception:  # pragma: no cover - telegram always installed in prod
        return None
    resolved = story_cta(payload, lane)
    label = CTA_MARKER if resolved["story_url"] else "More on this story"
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"{label} \u2192", url=resolved["url"])]])
