"""Longform transform — Telegram-HTML report → Ghost markdown/lexical.

The three daily reports (Morning Brief, Midday Market Report, Evening Wrap)
are authored as Telegram HTML: a ruled masthead banner
(``<b>━━━ EDITION ━━━</b>``), ruled section banners, ``## SECTION`` headers,
``• `` bullets, ``<a href="URL">text</a>`` links and ``<b>/<i>`` emphasis.

Telegram markup is NOT valid longform. Before a report is mirrored to the
Ghost blog (``_vendor.content_platform.create_post``) it must be transformed
into clean markdown: H1 masthead, H2/H3 sections, real ``[text](url)`` links,
no ``<b>``/``<i>``/``<a>`` tags, no ruled banners, single-blank-line spacing.

This module is the single canonical transform. It is deliberately
dependency-free (stdlib only) so it can never fail on a broken vendor import,
and it is pure (no I/O) so it is trivially testable.

Contract:
  * ``to_markdown(report)`` → markdown string (H1/H2/H3, links, spacing).
  * ``to_lexical(report)``  → Ghost lexical JSON string (for ``create_post``).

Both are idempotent: running the transform on already-markdown input is safe.
"""

from __future__ import annotations

import html
import re

#: Ruled Telegram banner: ``<b>━━━ SECTION ━━━</b>`` (box-drawing decoration).
_RULED_BANNER_RE = re.compile(r"^\s*<b>\s*([━─=•·]+)\s*(.*?)\s*\1\s*</b>\s*$", re.DOTALL)
#: Box-drawing / rule decoration characters stripped from a banner label.
_BOX_DECOR_RE = re.compile(r"[━─=•·]+")
#: Markdown ATX heading (``## SECTION``).
_ATX_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
#: A line that is ENTIRELY a bold span — the LLM's section-header shape
#: (``<b>WHAT RUGGED TODAY</b>``). Rendered as an H2 when not the masthead.
_BOLD_ONLY_RE = re.compile(r"^\s*<b>\s*(.+?)\s*</b>\s*$", re.DOTALL | re.IGNORECASE)
#: Telegram inline tags.
_BOLD_RE = re.compile(r"<b>(.*?)</b>", re.DOTALL | re.IGNORECASE)
_ITALIC_RE = re.compile(r"<i>(.*?)</i>", re.DOTALL | re.IGNORECASE)
_LINK_RE = re.compile(r'<a\s+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
#: Any residual HTML tag (defensive — nothing should survive the passes above).
_ANY_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
#: Bullet marker (``-``, ``•``, ``*``), possibly repeated.
_BULLET_RE = re.compile(r"^[-•*](?:\s*[-•*])*\s+")
#: Collapse 3+ newlines to a single blank line (no double-spacing).
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _strip_inline(text: str) -> str:
    """Convert Telegram inline tags to markdown, decode entities, drop the rest."""
    text = _LINK_RE.sub(lambda m: f"[{m.group(2).strip()}]({m.group(1).strip()})", text)
    text = _BOLD_RE.sub(lambda m: f"**{m.group(1).strip()}**", text)
    text = _ITALIC_RE.sub(lambda m: f"*{m.group(1).strip()}*", text)
    text = _ANY_TAG_RE.sub("", text)
    return html.unescape(text).strip()


def _banner_label(line: str) -> str | None:
    """``<b>━━━ MARKET OVERVIEW ━━━</b>`` → ``MARKET OVERVIEW`` (None if not a banner)."""
    m = _RULED_BANNER_RE.match(line)
    if not m:
        return None
    label = _BOX_DECOR_RE.sub("", m.group(2)).strip()
    return label or None


def to_markdown(report: str) -> str:
    """Transform a Telegram-HTML report into clean longform markdown.

    * The first ruled banner (the masthead) becomes ``# EDITION``.
    * Every later ruled banner becomes ``## SECTION``.
    * ``## SECTION`` ATX headers are preserved as-is.
    * ``• `` bullets become ``- `` markdown list items.
    * ``<a href>`` → ``[text](url)``; ``<b>`` → ``**``; ``<i>`` → ``*``.
    * Blank runs collapse to a single blank line; entities are decoded.
    """
    if not report or not report.strip():
        return ""
    text = _BR_RE.sub("\n", report)
    text = _BLANK_RUN_RE.sub("\n\n", text)

    out: list[str] = []
    masthead_done = False
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue

        first = lines[0]
        banner = _banner_label(first)
        atx = _ATX_RE.match(first)
        if banner:
            level = "#" if not masthead_done else "##"
            masthead_done = True
            out.append(f"{level} {banner}")
            lines = lines[1:]
        elif atx:
            out.append(f"{atx.group(1)} {_strip_inline(atx.group(2))}")
            lines = lines[1:]
        if not lines:
            continue

        # Walk the remaining lines: a standalone bold line is a section
        # header (H2); consecutive bullets form a markdown list; anything
        # else is a paragraph line.
        for ln in lines:
            bold_only = _BOLD_ONLY_RE.match(ln)
            if bold_only and masthead_done:
                out.append(f"## {_strip_inline(bold_only.group(1))}")
            elif _BULLET_RE.match(ln):
                out.append(f"- {_strip_inline(_BULLET_RE.sub('', ln))}")
            else:
                out.append(_strip_inline(ln))
        out.append("")  # blank line between blocks

    return _BLANK_RUN_RE.sub("\n\n", "\n".join(out)).strip()


__all__ = ["to_markdown"]
