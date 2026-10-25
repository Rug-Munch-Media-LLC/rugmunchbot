"""HTML construction helpers for Telegram ``parse_mode=HTML``.

Telegram accepts a small tag set (``b``, ``i``, ``u``, ``s``, ``a``, ``code``,
``pre``) and rejects a message with unbalanced or unknown tags. Every value
that reaches a post goes through :func:`esc`; every link goes through
:func:`link` so the href is attribute-escaped and the label is text-escaped.
"""

from __future__ import annotations

import html
from typing import Final

from rugmunchbot.content_style._rules import HEADING_RULE, LINK_LABEL_MAX

__all__ = ["bold", "esc", "heading", "italic", "link"]

#: Tags Telegram renders. Anything else is stripped by :func:`sanitize_tags`.
ALLOWED_TAGS: Final[frozenset[str]] = frozenset(
    {"b", "strong", "i", "em", "u", "s", "code", "pre", "a", "blockquote"}
)


def esc(value: object) -> str:
    """Escape text for a Telegram HTML text node (quotes left intact)."""
    return html.escape(str(value), quote=False)


def link(url: str, label: str, *, limit: int = LINK_LABEL_MAX) -> str:
    """Canonical anchor: attribute-escaped href, text-escaped clipped label."""
    href = html.escape(str(url or "").strip(), quote=True)
    text = esc(label).strip()
    if len(text) > limit:
        cut = text[:limit]
        space = cut.rfind(" ")
        text = (cut[:space] if space > limit // 2 else cut).rstrip(" ,;:-") + "\u2026"
    return f'<a href="{href}">{text}</a>'


def bold(value: object) -> str:
    """``<b>value</b>`` with the value escaped."""
    return f"<b>{esc(value)}</b>"


def italic(value: object) -> str:
    """``<i>value</i>`` with the value escaped."""
    return f"<i>{esc(value)}</i>"


def heading(value: object) -> str:
    """Canonical ruled-banner heading: ``<b>━━━ VALUE ━━━</b>``."""
    return f"<b>{HEADING_RULE} {esc(value)} {HEADING_RULE}</b>"
