"""HTML-safe splitting for the Telegram 4096-character ceiling.

One implementation for every surface (daily briefs, wrap editions, market
reports, channel posts). The five hand-rolled splitters this replaces cut at
``remaining[:3900]`` and then patched ``cut.endswith("<")`` — which still
sliced tags, entities and paragraphs in half, and left a later part with a
closing tag whose opener lived in an earlier message.

Guarantees: no part exceeds the limit, no tag is cut, no entity is cut, and
every part is self-balanced — tags open at a cut are closed at the end of
that part and re-opened at the start of the next.
"""

from __future__ import annotations

import re

from rugmunchbot.content_style._normalize import balance_tags
from rugmunchbot.content_style._rules import SAFE_LIMIT

__all__ = ["split_html"]

_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>")
_PAIRED = frozenset({"b", "strong", "i", "em", "u", "s", "code", "pre", "a", "blockquote"})
_ENTITY_TAIL_RE = re.compile(r"&[#a-zA-Z0-9]{1,10}$")
_MAX_PARTS = 200


def _safe_cut(text: str, limit: int) -> int:
    """Largest cut index <= *limit* that is outside a tag and an entity."""
    cut = max(min(limit, len(text)), 1)
    lt = text.rfind("<", 0, cut)
    if lt != -1 and text.find(">", lt, cut) == -1:
        cut = max(lt, 1)  # cut landed inside a tag — back off to its start
    amp = text.rfind("&", 0, cut)
    if amp != -1 and _ENTITY_TAIL_RE.match(text[amp:cut]):
        cut = max(amp, 1)  # never split an HTML entity
    for delim in ("\n\n", "\n"):
        idx = text.rfind(delim, 0, cut)
        if idx > limit * 0.5:
            return idx
    space = text.rfind(" ", 0, cut)
    return space if space > limit * 0.6 else cut


def _open_tags(fragment: str, stack: list[str] | None = None) -> list[str]:
    """Paired tags left open at the end of *fragment*, from an initial *stack*."""
    open_stack: list[str] = list(stack or [])
    for m in _TAG_RE.finditer(fragment):
        closing, name, self_closing = m.group(1), m.group(2).lower(), m.group(3)
        if name not in _PAIRED or self_closing:
            continue
        if not closing:
            open_stack.append(name)
        elif name in open_stack:
            while open_stack and open_stack.pop() != name:
                continue
    return open_stack


def _closers(stack: list[str]) -> str:
    """Closing tags for *stack*, innermost first."""
    return "".join(f"</{name}>" for name in reversed(stack))


def _openers(stack: list[str]) -> str:
    """Opening tags re-establising *stack* (outermost first)."""
    return "".join(f"<{name}>" for name in stack)


def split_html(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """Split *text* into <= limit, self-balanced parts without breaking tags."""
    if not text:
        return []
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    remaining = text
    stack: list[str] = []
    guard = 0
    while remaining and guard < _MAX_PARTS:
        guard += 1
        prefix = _openers(stack)
        if len(prefix) + len(remaining) <= limit:
            parts.append(balance_tags(prefix + remaining))
            remaining = ""
            break
        budget = max(limit - len(prefix) - 32, 200)
        cut = _safe_cut(remaining, budget)
        chunk = remaining[:cut].rstrip()
        stack_next = _open_tags(chunk, stack)
        candidate = prefix + chunk + _closers(stack_next)
        while len(candidate) > limit and cut > 200:
            cut = _safe_cut(remaining, cut - (len(candidate) - limit) - 1)
            chunk = remaining[:cut].rstrip() or remaining[:cut]
            stack_next = _open_tags(chunk, stack)
            candidate = prefix + chunk + _closers(stack_next)
        if not chunk:
            break
        stack = stack_next
        parts.append(candidate)
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        parts.append(balance_tags(_openers(stack) + remaining))
    return parts
