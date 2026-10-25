"""Canonical footer / CTA rows — one shape for every surface.

The story CTA already has a single source: :mod:`rugmunchbot.channels._story_cta`
(``https://rugmunch.io/story/<slug>`` + UTM attribution). This module does NOT
re-implement it — it composes that CTA with the product rows so a post closes
the same way everywhere.

Contract: at most TWO rows. Row 1 is the story CTA when a slug/lane resolves,
otherwise the site CTA. Row 2 is always the bot CTA.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.content_style._rules import BOT_CTA, PARAGRAPH_GAP, SITE_CTA

__all__ = ["footer_lines"]

#: Lanes whose canonical section page is not the feed (kept here so a caller
#: never has to invent one). Mirrors ``_story_cta.SECTION_PAGES``.
_FALLBACK_LANES = frozenset({"morning", "midday", "evening", "daily", "news", "scan", "alerts"})


def footer_lines(payload: dict[str, Any] | None = None, lane: str = "") -> list[str]:
    """Canonical closing rows for a channel post (story CTA + bot CTA)."""
    rows: list[str] = []
    try:
        from rugmunchbot.channels._story_cta import cta_line, story_cta

        resolved = story_cta(payload or {}, lane or "daily")
        if resolved.get("url"):
            rows.append(cta_line(payload or {}, lane or "daily"))
        else:
            rows.append(SITE_CTA)
    except Exception:  # fail-open: a footer must never block a send
        rows.append(SITE_CTA)
    rows.append(BOT_CTA)
    return rows


def footer_block(payload: dict[str, Any] | None = None, lane: str = "") -> str:
    """Footer rows joined with the canonical paragraph gap."""
    return PARAGRAPH_GAP.join(footer_lines(payload, lane))
