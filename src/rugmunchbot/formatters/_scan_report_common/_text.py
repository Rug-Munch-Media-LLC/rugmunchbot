"""Generic string utils shared by every scan-report section module."""

from __future__ import annotations

import re


def _clip(text: object, limit: int) -> str:
    """Truncate ``text`` to ``limit`` chars, adding a leading-ellipsis mark
    (``…``) ONLY when content was actually cut. Avoids the mid-tag / mid-word
    garbage that a bare ``[:n]`` can leave in a Telegram-HTML payload."""
    s = str(text or "").strip()
    if len(s) <= limit:
        return s
    return s[: limit - 1].rstrip() + "\u2026"


def _collapse_blank_lines(text: str) -> str:
    """Collapse runs of 3+ newlines to 2 (one blank line).

    Guard for section interleaving: a red-alert banner followed by the next
    section header used to render a double blank line. Pure string pass, so
    every section keeps its own leading blank without stacking extras.
    """
    return re.sub(r"\n{3,}", "\n\n", text)


def _short_addr(addr: str) -> str:
    """Compact ``0xAb12...34cd`` form for long wallet addresses."""
    addr = str(addr)
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"
