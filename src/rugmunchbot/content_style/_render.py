"""The canonical post renderer.

Every content surface builds a post through :func:`render_post`: one heading
style, one paragraph gap, canonical bullets, escaped values, links attached to
their labels (never bare URLs) and one footer. Sections are explicit, so a
caller cannot accidentally emit two blank lines, a wall of text or a second
footer.
"""

from __future__ import annotations

from collections.abc import Iterable  # noqa: TC003
from typing import Any

from rugmunchbot.content_style._escape import bold, heading
from rugmunchbot.content_style._footer import footer_block
from rugmunchbot.content_style._normalize import normalize
from rugmunchbot.content_style._rules import BULLET, PARAGRAPH_GAP

__all__ = ["bullets", "render_post"]

#: A daily/section post never carries more summary lines than this.
MAX_SUMMARY_LINES = 5


def bullets(lines: Iterable[str]) -> str:
    """Render *lines* as canonical bullets (empty lines dropped)."""
    return "\n".join(f"{BULLET}{line.strip()}" for line in lines if line and line.strip())


def _key_line(text: str) -> str:
    """One metric/chart line, prefixed with the canonical chart glyph."""
    line = str(text or "").strip()
    if not line:
        return ""
    if line[0] in "\U0001f4c8\U0001f4c9\U0001f4ca":
        return line
    return f"\U0001f4ca {line}"


def render_post(
    *,
    title: str = "",
    summary: Iterable[str] | str = (),
    key_line: str = "",
    body: str = "",
    payload: dict[str, Any] | None = None,
    lane: str = "",
    footer: bool = True,
) -> str:
    """Assemble one canonical post. Pure; layout only, never invents content.

    ``summary`` accepts 3-5 paragraphs (a string is split on blank lines).
    ``key_line`` is the single chart/token metric row, rendered only when the
    caller has a real value. ``body`` is free-form pre-formatted HTML that is
    normalized but not reordered.
    """
    if isinstance(summary, str):
        summary_lines = [p for p in summary.split("\n\n") if p.strip()]
    else:
        summary_lines = [str(p).strip() for p in summary if str(p).strip()]
    blocks: list[str] = []
    if title.strip():
        blocks.append(heading(title.strip()))
    blocks.extend(summary_lines[:MAX_SUMMARY_LINES])
    metric = _key_line(key_line)
    if metric:
        blocks.append(metric)
    if body and body.strip():
        blocks.append(body.strip())
    if footer:
        blocks.append(footer_block(payload, lane))
    return normalize(PARAGRAPH_GAP.join(block for block in blocks if block))


def _title_or_bold(title: str) -> str:
    """Heading when the title is plain text, passthrough when already HTML."""
    return title if "<b>" in title else bold(title)
