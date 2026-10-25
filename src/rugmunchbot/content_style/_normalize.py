"""Canonical text normalization — paragraph spacing, bullets, links, tags.

One pass, pure, fail-open. It only ever changes LAYOUT: no fact, number,
token symbol, URL target or sentence is added, removed or reordered. Used by
the format-law pre-send gate and by every splitter so a post looks identical
whichever surface produced it.

Primary entry point: :func:`normalize`.
"""

from __future__ import annotations

import re

from rugmunchbot.content_style._rules import (
    BULLET,
    BULLET_ALIASES,
    EMOJI_ALLOWLIST,
    PARAGRAPH_GAP,
)

__all__ = ["balance_tags", "dedupe_links", "normalize"]

_BLANK_RUN_RE = re.compile(r"\n{3,}")
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.MULTILINE)
#: A bullet line: leading indent + one alias glyph + whitespace.
_BULLET_RE = re.compile(
    r"^([ \t]*)(?:[" + re.escape("".join(BULLET_ALIASES)) + r"]|\u2022)\s+(?=\S)"
)
#: Ruled banner line, with or without the bold wrapper.
_BANNER_RE = re.compile(r"^\s*(?:<b>)?\s*\u2501{3}.*\u2501{3}\s*(?:</b>)?\s*$")
_ANCHOR_RE = re.compile(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)\b[^>]*?(/?)>")
_EMOJI_RUN_RE = re.compile(r"([\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF])\1+")
_PAIRED = frozenset({"b", "strong", "i", "em", "u", "s", "code", "pre", "a", "blockquote"})


def _bullets(text: str) -> str:
    """Rewrite every bullet alias to the canonical glyph and indent."""
    return "\n".join(
        _BULLET_RE.sub(lambda m: m.group(1) + BULLET, line) for line in text.split("\n")
    )


def _banner_gaps(text: str) -> str:
    """Guarantee exactly one blank line around a ruled-banner heading."""
    lines = text.split("\n")
    out: list[str] = []
    for idx, line in enumerate(lines):
        if _BANNER_RE.match(line) and out and out[-1].strip():
            out.append("")
        out.append(line)
        nxt = lines[idx + 1] if idx + 1 < len(lines) else ""
        if _BANNER_RE.match(line) and nxt.strip() and not _BANNER_RE.match(nxt):
            out.append("")
    return "\n".join(out)


def dedupe_links(text: str) -> str:
    """Keep the first anchor per URL; later repeats become plain labels.

    Guarantees the "one link per URL" rule without dropping the sentence that
    carried the repeat (data is preserved, only the hyperlink is dropped).
    """
    seen: set[str] = set()

    def _keep(match: re.Match[str]) -> str:
        href = match.group(1)
        if href in seen:
            return match.group(2)
        seen.add(href)
        return match.group(0)

    return _ANCHOR_RE.sub(_keep, text)


def balance_tags(text: str) -> str:
    """Close unclosed Telegram tags and drop stray closers (fail-open)."""
    stack: list[str] = []
    for m in _TAG_RE.finditer(text):
        closing, name, self_closing = m.group(1), m.group(2).lower(), m.group(3)
        if name not in _PAIRED or self_closing:
            continue
        if closing:
            if name in stack:
                while stack and stack.pop() != name:
                    continue
        else:
            stack.append(name)
    if not stack:
        return text
    return text.rstrip() + "".join(f"</{name}>" for name in reversed(stack))


def _is_emoji(ch: str) -> bool:
    """True for pictographic emoji / dingbats (not arrows or digits)."""
    return (
        "\U0001f000" <= ch <= "\U0001faff"
        or "\u2600" <= ch <= "\u27bf"
        or "\u2b00" <= ch <= "\u2bff"
    )


def _scrub_emoji(text: str) -> str:
    """Drop emoji that are not on the canonical allowlist (opt-in).

    Variation selectors, ZWJ and keycap combiners survive so an allowlisted
    sequence keeps rendering as one glyph.
    """
    keep_joiners = {"\ufe0f", "\u200d", "\u20e3"}
    return "".join(
        ch for ch in text if ch in EMOJI_ALLOWLIST or ch in keep_joiners or not _is_emoji(ch)
    )


def normalize(text: str, *, scrub_emoji: bool = False, links: bool = True) -> str:
    """Return *text* in canonical Telegram layout. Pure, idempotent, no I/O.

    Rules: one blank line between paragraphs (never two), canonical bullets,
    one blank line around ruled banners, one link per URL, balanced tags,
    no trailing whitespace. Facts never change — only layout.
    """
    if not text or not text.strip():
        return text or ""
    try:
        out = _TRAILING_WS_RE.sub("", text.replace("\r\n", "\n"))
        out = _EMOJI_RUN_RE.sub(r"\1", out)
        if scrub_emoji:
            out = _scrub_emoji(out)
        out = _bullets(out)
        out = _banner_gaps(out)
        out = _BLANK_RUN_RE.sub(PARAGRAPH_GAP, out)
        out = balance_tags(out)
        if links:
            out = dedupe_links(out)
        return out.strip()
    except Exception:  # fail-open: layout must never block a send
        return text
