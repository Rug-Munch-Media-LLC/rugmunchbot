"""chat_ai markdown table → bullet-list fallback.

Telegram HTML has no table element; a raw markdown pipe table would render
as a wall of literal ``|`` characters. Extracted verbatim from the former
single-module ``chat_ai/_markup.py``.
"""

from __future__ import annotations

import re

from rugmunchbot.handlers.chat_ai._parts import _is_fence_line

_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")


def _split_table_row(row: str) -> list[str]:
    """Split a markdown table row into trimmed cells (drop outer pipes)."""
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _is_table_block(rows: list[str]) -> bool:
    """True when consecutive pipe rows look like a markdown table.

    A separator row (``|---|``) is the definitive signal; a run of 3+ pipe
    rows without one (some LLMs skip the separator) is treated as a table
    too. Single stray pipe lines are left alone.
    """
    if len(rows) < 2:
        return False
    if any(_TABLE_SEP_RE.match(r) for r in rows):
        return True
    return len(rows) >= 3


def _render_table_block(rows: list[str]) -> list[str]:
    """Render a markdown table block as compact bullet lines (raw text).

    The header row (first row, when a separator row is present) becomes a
    ``**a · b**`` line so the bold conversion renders it as a header; each
    data row becomes ``• a · b · c``. The ``|---|`` separator row is
    dropped. ``·`` (U+00B7) keeps rows on one line without the pipe-wall
    look of the original table.
    """
    has_separator = len(rows) >= 2 and bool(_TABLE_SEP_RE.match(rows[1]))
    rendered: list[str] = []
    for idx, row in enumerate(rows):
        if _TABLE_SEP_RE.match(row):
            continue
        cells = _split_table_row(row)
        if not cells or any(not c for c in cells):
            continue
        if has_separator and idx == 0:
            rendered.append(f"**{' · '.join(cells)}**")
        else:
            rendered.append(f"\u2022 {' · '.join(cells)}")
    return rendered


def _tables_to_lists(text: str) -> str:
    """Convert markdown table blocks to compact bullet lists (raw text).

    Detect table blocks (consecutive ``|`` rows, with or without a
    ``|---|`` separator row) and render each row as ``• a · b · c`` with
    the header bolded. Fenced code blocks are never touched: pipe-shaped
    lines inside ````` ``` ```` stay verbatim.
    """
    lines = text.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if _is_fence_line(line):
            in_fence = not in_fence
        if in_fence or not _TABLE_ROW_RE.match(line):
            out.append(line)
            i += 1
            continue
        run: list[str] = []
        while i < n and _TABLE_ROW_RE.match(lines[i]):
            run.append(lines[i])
            i += 1
        if not _is_table_block(run):
            out.extend(run)
            continue
        out.extend(_render_table_block(run))
    return "\n".join(out)
