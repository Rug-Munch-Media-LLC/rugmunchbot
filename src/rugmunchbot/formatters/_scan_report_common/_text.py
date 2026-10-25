"""Generic string utils shared by every scan-report section module."""

from __future__ import annotations

import re

from rugmunchbot.formatters._base._primitives import (
    clip as _clip_canonical,
    short_addr as _short_addr,
)


def _clip(text: object, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars with a trailing ellipsis (canonical).

    Delegates to :func:`formatters._base._primitives.clip` so every section
    truncates identically — the local copies drifted (one appended the marker
    even when nothing was cut, another sliced mid-tag when the cut landed
    inside an HTML tag).
    """
    return _clip_canonical(text, limit)


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of 3+ newlines to 2 (one blank line).

    Guard for section interleaving: a red-alert banner followed by the next
    section header used to render a double blank line. Pure string pass, so
    every section keeps its own leading blank without stacking extras.
    """
    return re.sub(r"\n{3,}", "\n\n", text)


__all__ = ["_clip", "_collapse_blank_lines", "_short_addr"]
