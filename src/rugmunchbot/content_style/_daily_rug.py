"""The Daily Rug post shape — the canonical morning brief envelope.

Contract (2026-10-24): title, a 3-5 line summary, ONE key chart/token line,
the canonical story link (``https://rugmunch.io/story/<slug>``) and the CTA.

Content is never synthesised: the summary lines and the key line come from the
caller (the brief body and the gathered data). A missing key line is omitted
rather than invented, and a missing slug degrades to the lane section page via
:mod:`rugmunchbot.channels._story_cta`.
"""

from __future__ import annotations

from collections.abc import Iterable  # noqa: TC003

from rugmunchbot.content_style._normalize import normalize
from rugmunchbot.content_style._render import render_post
from rugmunchbot.content_style._rules import PARAGRAPH_GAP

__all__ = ["daily_rug_envelope", "daily_rug_post"]

#: Canonical edition title (mirrors ``daily_rug._prompt.EDITION``).
EDITION_TITLE = "The Daily Rug \u2014 Morning Market Brief"


def daily_rug_envelope(
    *,
    date_label: str,
    summary: Iterable[str] | str,
    key_line: str = "",
    slug: str = "",
    body: str = "",
) -> str:
    """Render the canonical Daily Rug post (title + summary + key line + body)."""
    return render_post(
        title=EDITION_TITLE,
        summary=summary,
        key_line=key_line,
        body=body,
        payload={"story_slug": slug} if slug else {},
        lane="morning",
    )


def daily_rug_post(
    *,
    date_label: str,
    summary: Iterable[str] | str,
    key_line: str = "",
    slug: str = "",
    body: str = "",
) -> str:
    """Alias kept explicit for call sites: the full Daily Rug post."""
    text = daily_rug_envelope(
        date_label=date_label, summary=summary, key_line=key_line, slug=slug, body=body
    )
    stamp = f"<i>{date_label}</i>" if date_label else ""
    if not stamp:
        return text
    head, sep, rest = text.partition(PARAGRAPH_GAP)
    return normalize(f"{head}{sep}{stamp}{sep}{rest}") if sep else text
