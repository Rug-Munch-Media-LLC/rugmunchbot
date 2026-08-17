"""Safe multi-part Telegram answer splitting + sending.

Two responsibilities:

  * :func:`split_in_parts` — PURE: split long RAW answer text into
    ``<= MAX_PART_CHARS`` parts without breaking markdown links, bare URLs,
    or fenced code blocks (````` ``` ... ``` `````). Falls back to the
    sentence/paragraph boundaries used by ``_helpers._chunk_text``.
  * :func:`send_in_parts` — ASYNC: deliver the parts as separate Telegram
    messages, attaching the keyboard only to the last part and degrading to
    plain text when a part is rejected (e.g. unbalanced HTML entities).

The splitter runs on the RAW (pre-escape) text so code fences and links stay
intact for the human reader; ``handlers/chat_ai/__init__.py`` escapes +
converts ``**bold**`` per chunk afterwards exactly as before.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from telegram.error import TelegramError

if TYPE_CHECKING:
    from telegram import Message
    from telegram._utils.types import ReplyMarkup

# Telegram hard limit is 4096 chars; leave headroom for entity expansion.
MAX_PART_CHARS = 4000

# A fenced code block: ``` ... ``` (non-greedy, DOTALL).
_FENCE_RE = re.compile(r"`{3,}.*?`{3,}", re.DOTALL)
# A markdown link [text](url) or a bare URL.
_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)|https?://[^\s)\]<>]+")
# A complete HTML anchor <a href="...">label</a> (raw HTML a model emitted;
# the pipeline does not convert <a> tags, so they must stay atomic too).
_ANCHOR_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.DOTALL)
# A balanced **bold** / ***bold*** span (must mirror _helpers._BOLD_RE).
_BOLD_SPAN_RE = re.compile(r"\*{2,3}[^*\n]+?\*{2,3}")


def _is_fence_line(line: str) -> bool:
    """True when ``line`` opens/closes a fenced code block (```...)."""
    return bool(re.match(r"^\s*`{3,}", line))


# A complete HTML tag <b> / </b> / <i> / ... (used by the balance check).
_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*\b[^>]*>")


def _last_unclosed_opener(text: str) -> int:
    """Index of the last unclosed '<tag>' opener in ``text`` (or -1).

    Walks ``text`` with a tag stack: an opener pushes, a matching closer
    pops. When the walk ends with tags still open, the last opener never got
    closed — a ``<b>...`` cut mid-content. Returns its start index so the
    caller can back up to it. Only simple non-void tags are produced by the
    chat renderer (``<b>``/``</b>``/``<i>``/``</i>``), so a stack is enough.
    """
    stack: list[tuple[str, int]] = []
    for m in _TAG_RE.finditer(text):
        raw = m.group(0)
        name = raw.lstrip("</").rstrip(">").strip().split()[0].lower()
        if raw.startswith("</"):
            if stack and stack[-1][0] == name:
                stack.pop()
        else:
            stack.append((name, m.start()))
    return stack[-1][1] if stack else -1


def _safe_clip(text: str, limit: int) -> str:
    """Clip ``text`` to ``limit`` chars without splitting an HTML tag/entity.

    The final safety net in :func:`send_in_parts`: the escaped + bolded parts
    passed in can exceed ``MAX_PART_CHARS`` (escape expansion), and a naive
    ``text[:limit]`` can slice mid-entity (``&am``), mid-tag (``<b``), or
    mid-element (``<b>Risk: 42/100. Check`` with no ``</b>``) — unbalanced
    HTML that Telegram rejects (the whole part then falls back to plain
    text). When the cut point leaves a dangling entity or unclosed tag, back
    up to just before it so every part stays valid HTML.
    """
    if len(text) <= limit:
        return text
    chunk = text[:limit]
    # Never end mid-entity (&am...): back up to before the dangling '&'.
    amp = chunk.rfind("&")
    if amp != -1 and chunk.find(";", amp) == -1:
        chunk = chunk[:amp]
    # Never end mid-tag (<b, </b, <i...): back up to before the dangling '<'.
    lt = chunk.rfind("<")
    if lt != -1 and chunk.find(">", lt) == -1:
        chunk = chunk[:lt]
    # Never end inside an open element (<b>Risk: 42/100. Check — no </b> yet).
    opener = _last_unclosed_opener(chunk)
    if opener != -1:
        chunk = chunk[:opener]
    return chunk.rstrip()


def _ends_inside_fence(text: str) -> bool:
    """True when ``text`` ends with an unclosed fence (odd fence lines)."""
    return sum(1 for line in text.split("\n") if _is_fence_line(line)) % 2 == 1


def _split_segments(text: str) -> list[tuple[bool, str]]:
    """Split ``text`` into ``(is_code, segment)`` spans.

    Each fenced code block becomes one atomic ``is_code=True`` segment; all
    other text is grouped into ``is_code=False`` prose segments. An unclosed
    fence (LLM output bug) leaves the whole text as prose — safe degradation.
    """
    segments: list[tuple[bool, str]] = []
    pos = 0
    for m in _FENCE_RE.finditer(text):
        if m.start() > pos:
            segments.append((False, text[pos : m.start()]))
        segments.append((True, m.group(0)))
        pos = m.end()
    if pos < len(text):
        segments.append((False, text[pos:]))
    return segments


def _split_code(segment: str, max_chars: int) -> list[str]:
    """Hard-split a single oversized fenced code block.

    Each returned part keeps its fences balanced: a part cut mid-block is
    closed with a trailing ````` ``` ````` and the next part re-opens one, so
    the markdown never dangles. Only called when the block alone exceeds
    ``max_chars`` (normal code blocks stay atomic).
    """
    # Reserve room for the synthetic closing fence (``\n````` = 4 chars) so a
    # part never exceeds ``max_chars`` after re-balancing.
    capacity = max(8, max_chars - 4)
    if len(segment) <= capacity:
        return [segment]
    lines = segment.split("\n")
    parts: list[str] = []
    buf = ""
    open_fence = False
    for line in lines:
        is_fence = _is_fence_line(line)
        if is_fence:
            open_fence = not open_fence
        candidate = f"{buf}\n{line}" if buf else line
        if len(candidate) <= capacity:
            buf = candidate
            continue
        if buf:
            parts.append(f"{buf}\n```" if _ends_inside_fence(buf) else buf)
        if is_fence:
            buf = line  # real fence line starts/ends the next part naturally
        elif open_fence:
            buf = f"```\n{line}"  # cut mid-block: re-open the fence
        else:
            buf = line
    if buf:
        # A lone closing fence is complete on its own — do not re-close it.
        if len(buf.split("\n")) == 1 and _is_fence_line(buf):
            parts.append(buf)
        else:
            parts.append(f"{buf}\n```" if _ends_inside_fence(buf) else buf)
    return parts


def _cut_index(text: str, limit: int) -> int:
    """Safe cut index for a prose segment: the last sentence boundary before
    ``limit`` (else the last word boundary, else exactly ``limit``) — never
    inside a markdown link / bare URL / HTML anchor."""
    best = -1
    for sep_ in (". ", "? ", "! "):
        idx = text.rfind(sep_, 0, limit)
        if idx >= 0:
            best = max(best, idx + len(sep_))
    cut = best if best > limit // 2 else text.rfind(" ", 0, limit)
    if cut < limit // 2:
        cut = limit
    # Never split a link: back up to the link's start when the cut lands
    # inside one (only when the backed-up piece is still substantial);
    # otherwise move the cut FORWARD past the link when it still fits in the
    # window — a whole link beats a tiny part OR a mid-URL break. A link that
    # spans the whole window keeps the cut (unavoidable; the renderer shows
    # the literal remainder).
    for regex in (_LINK_RE, _ANCHOR_RE):
        for m in regex.finditer(text):
            if m.start() < cut < m.end():
                if m.start() >= limit // 2:
                    cut = m.start()
                elif m.end() <= limit:
                    cut = m.end()
                break
    # Never split a **bold** span: a cut landing inside one would leave a
    # dangling opener/closer at a part edge. Back up to the span's opening
    # so the whole span leads the next part (balanced). A span that starts
    # too early to back up to keeps the cut — the renderer tolerates the
    # unbalanced markers as literal text (see _markdown_to_html).
    for m in _BOLD_SPAN_RE.finditer(text):
        if m.start() < cut < m.end():
            if m.start() >= limit // 2:
                cut = m.start()
            break
    return cut


def _split_prose(segment: str, max_chars: int) -> list[str]:
    """Split a prose segment (no fences) into ``<= max_chars`` pieces,
    preferring sentence boundaries and never splitting a link."""
    if len(segment) <= max_chars:
        return [segment]
    parts: list[str] = []
    remaining = segment
    while len(remaining) > max_chars:
        cut = _cut_index(remaining, max_chars)
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        parts.append(remaining)
    return parts


def split_in_parts(text: str, max_chars: int = MAX_PART_CHARS) -> list[str]:
    """Split ``text`` into Telegram-sized parts without breaking links or
    fenced code blocks.

    Operates on RAW text (pre-escape). Paragraphs are the safest split points;
    a fenced code block stays atomic unless it alone exceeds ``max_chars``
    (then it is hard-split with balanced fences); a link is never split.

    >>> split_in_parts("short") == ["short"]
    True
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    current = ""
    for is_code, segment in _split_segments(text):
        pieces = _split_code(segment, max_chars) if is_code else _split_prose(segment, max_chars)
        for piece in pieces:
            if current and len(current) + len(piece) + 2 > max_chars:
                parts.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        parts.append(current)
    return parts


async def send_in_parts(
    message: Message,
    parts: list[str] | str,
    *,
    parse_mode: str | None = None,
    reply_markup: ReplyMarkup | None = None,
    disable_web_page_preview: bool = False,
) -> None:
    """Send ``parts`` as separate Telegram messages (multi-part answers).

    The ``reply_markup`` (keyboard) is attached only to the LAST part. Each
    part is clipped to ``MAX_PART_CHARS`` as a final safety net and, if
    Telegram rejects it (e.g. unbalanced HTML entities from a border-case
    chunk), it is re-sent as plain text so the user still receives the
    answer. Never raises.
    """
    if isinstance(parts, str):
        parts = split_in_parts(parts)
    if not parts:
        return
    last = len(parts) - 1
    for i, part in enumerate(parts):
        chunk = _safe_clip(part, MAX_PART_CHARS)
        try:
            await message.reply_text(
                chunk,
                parse_mode=parse_mode,
                reply_markup=reply_markup if i == last else None,
                disable_web_page_preview=disable_web_page_preview,
            )
        except (TelegramError, ConnectionError, OSError, TimeoutError):
            try:
                await message.reply_text(chunk)
            except (TelegramError, ConnectionError, OSError, TimeoutError):
                return
