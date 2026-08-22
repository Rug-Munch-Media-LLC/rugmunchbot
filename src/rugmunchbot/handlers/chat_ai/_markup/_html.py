"""chat_ai markdown → Telegram HTML conversion + answer capping.

Ordered so nothing clobbers: escaped/raw HTML emphasis tags → markdown
markers first, then block constructs (headers/HRs/lists/tables), then inline
code, bold, italic. Extracted verbatim from the former single-module
``chat_ai/_markup.py``.
"""

from __future__ import annotations

import re
from html import escape as _html_escape, unescape as _html_unescape

from rugmunchbot.formatters import thin_sep
from rugmunchbot.handlers.chat_ai._markup._beautify import (
    _is_separator_line,
    _last_sentence_boundary,
    _unwrap_bold,
)
from rugmunchbot.handlers.chat_ai._markup._constants import _BOLD_RE, MAX_ANSWER_CHARS
from rugmunchbot.handlers.chat_ai._markup._normalize import (
    _RAW_HTML_TAG_TO_MARKDOWN,
    _raw_html_emphasis_to_markdown,
)
from rugmunchbot.handlers.chat_ai._markup._tables import (
    _TABLE_ROW_RE,
    _is_table_block,
    _render_table_block,
)
from rugmunchbot.handlers.chat_ai._parts import _is_fence_line

# ══════════════════════════════════════════════
# MARKDOWN → HTML EMPHASIS (see _markdown_to_html)
# Conversion order matters so nothing clobbers: inline code runs FIRST
# (backtick content is never re-processed by the bold/italic passes), bold
# runs before italic (the asterisks of a **bold** span are never seen as
# single-asterisk italics), and italic runs last. Each marker requires a
# balanced pair — unmatched markers pass through as literal text.
# ══════════════════════════════════════════════
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# Markdown link [text](url) / bare URL / raw HTML anchor <a href="...">…</a>
# — a cap or chunk cut must never split one of these (mirrors _parts.py).
_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)|https?://[^\s)\]<>]+")
_ANCHOR_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.DOTALL)
# Single-asterisk italic; the inner span must start/end on a non-space char
# so arithmetic like ``x * 2 * y`` and stray ``* item`` bullets never match.
_ITALIC_RE = re.compile(r"\*([^*\s][^*\n]*?[^*\s])\*")
# Underscore italic is word-boundary guarded (intraword ``_snake_case_`` is
# NOT emphasis, matching standard markdown) and needs non-space ends too.
_UNDERSCORE_ITALIC_RE = re.compile(r"(?<![A-Za-z0-9])_([^_\s][^_\n]*?[^_\s])_(?![A-Za-z0-9])")

# ══════════════════════════════════════════════
# MARKDOWN BLOCK CONSTRUCTS (see _markdown_to_html)
# Telegram HTML has no header/table/HR elements — convert the block-level
# markdown local models emit (``## Header``, ``---``, pipe tables, numbered
# lists) into Telegram-safe HTML. ``_HEADER_RE`` matches ATX headers (1-3
# ``#``), stripping optional trailing ``#``s; ``_ORDERED_LIST_RE`` turns
# ``1. item`` into ``1) item`` (Telegram renders ``1.`` as a literal dot);
# ``_ESCAPED_HTML_TAG_RE`` is the defense-in-depth twin of
# ``_RAW_HTML_TAG_RE`` for tags that arrive already entity-escaped
# (``&lt;code&gt;`` — a double-escaped model or a pre-escaped caller).
# ══════════════════════════════════════════════
_HEADER_RE = re.compile(r"^#{1,3}\s+(.+?)\s*#*\s*$")
_ORDERED_LIST_RE = re.compile(r"^(\s*)(\d{1,3})\.(\s)")
_ESCAPED_HTML_TAG_RE = re.compile(r"&lt;/?\s*(b|strong|i|em|code|pre)\s*&gt;")


def _escape_inner(text: str) -> str:
    """Canonicalize the inner content of a markdown emphasis span.

    ``html.escape(html.unescape(text))``: raw specials (``<``, ``&``) get
    escaped, while already-escaped entities (``&amp;``, ``&lt;``) are
    preserved — so the conversion is safe standalone AND idempotent on the
    pre-escaped chunks the pipeline feeds it (no ``&amp;amp;`` double-escape).
    """
    return _html_escape(_html_unescape(text))


def _bold_italic_to_html(text: str) -> str:
    """Convert bold/italic markdown markers (no inline-code spans present).

    Bold runs first so a ``**bold**`` span's asterisks are never seen by the
    single-asterisk italic pass; the underscore-italic pass is word-boundary
    guarded so ``_snake_case_`` is left alone. Unmatched markers stay literal.
    """
    text = _BOLD_RE.sub(lambda m: f"<b>{_escape_inner(m.group(1))}</b>", text)
    text = _ITALIC_RE.sub(lambda m: f"<i>{_escape_inner(m.group(1))}</i>", text)
    text = _UNDERSCORE_ITALIC_RE.sub(lambda m: f"<i>{_escape_inner(m.group(1))}</i>", text)
    return text


def _escaped_html_emphasis_to_markdown(text: str) -> str:
    """Convert entity-escaped HTML emphasis tags back to markdown markers.

    Defense-in-depth twin of :func:`_raw_html_emphasis_to_markdown` for tags
    that arrive already escaped (``&lt;code&gt;`` — e.g. a model that
    double-escaped, or a direct caller that pre-escaped the text). Runs on
    the ESCAPED text inside :func:`_markdown_to_html`, so the conversion can
    never re-introduce raw HTML: the tags become backtick/``**`` markers and
    the emphasis passes below re-emit real, escaped ``<code>``/``<b>`` tags.
    """
    return _ESCAPED_HTML_TAG_RE.sub(
        lambda m: _RAW_HTML_TAG_TO_MARKDOWN.get(m.group(1).lower(), m.group(0)),
        text,
    )


def _markdown_line_to_html(line: str) -> str:
    """Convert one non-fence line's block-level markdown to Telegram HTML.

    - separator lines (``---`` / ``────`` / ``___``) → the thin separator
      (Telegram has no ``<hr>`` element);
    - ATX headers (``## Header`` / ``### Header``) → ``<b>Header</b>``
      (bold is the closest Telegram-safe header);
    - numbered-list items (``1. item``) → ``1) item`` (``1.`` renders as a
      literal dot otherwise).

    Emphasis markers inside a header are converted inline so ``## **Market
    Overview**`` never double-bolds; all other lines pass through untouched.
    """
    if _is_separator_line(line):
        return thin_sep()
    m = _HEADER_RE.match(line)
    if m:
        inner = m.group(1).strip()
        # A header that is ONE whole bold span (`## **Market Overview**`)
        # unwraps before the bold wrapper so it never double-bolds; mid-line
        # bold (`## **BTC** Price`) stays inline and nests harmlessly.
        if re.fullmatch(r"\*{2,3}.+?\*{2,3}", inner):
            inner = _unwrap_bold(inner)
        return f"<b>{_bold_italic_to_html(_escape_inner(inner))}</b>"
    return _ORDERED_LIST_RE.sub(r"\1\2)\3", line)


def _markdown_blocks_to_html(text: str) -> str:
    """Line-level markdown → HTML pass (headers, HRs, lists, tables).

    Handles the block constructs Telegram has no native element for, BEFORE
    the emphasis passes in :func:`_markdown_to_html`:

    - ``## / ###`` headers → ``<b>`` lines
    - ``---`` / ``────`` separators → the thin separator line
    - ``1.`` / ``2.`` numbered lists → ``1)`` / ``2)``
    - pipe tables → compact bullet blocks (same rendering as
      :func:`_tables_to_lists`, so a table that slipped past the raw-text
      pass — a direct caller, or a table embedded after escaping — still
      renders cleanly instead of a ``|`` wall)

    Fenced code blocks are never touched: their lines pass through verbatim.
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
        if in_fence:
            out.append(line)
            i += 1
            continue
        if _TABLE_ROW_RE.match(line):
            run: list[str] = []
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                run.append(lines[i])
                i += 1
            if _is_table_block(run):
                out.extend(_render_table_block(run))
                continue
            out.extend(_markdown_line_to_html(ln) for ln in run)
            continue
        out.append(_markdown_line_to_html(line))
        i += 1
    return "\n".join(out)


def _cap_answer(text: str, max_chars: int = MAX_ANSWER_CHARS) -> str:
    """Cap an answer to ``max_chars`` before Telegram chunking.

    A 1000-word wall for a simple query is never acceptable: when ``text``
    exceeds the cap it is trimmed at the last natural boundary inside the
    window (paragraph → sentence → word) and an ellipsis is appended so the
    truncation is honest. Never cuts mid-sentence, mid-link, mid-``**bold**``
    span, or mid-HTML tag. Returns the text unchanged when it already fits.
    """
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n\n", 0, max_chars)
    if cut < max_chars // 2:
        cut = _last_sentence_boundary(text, max_chars)
    if cut < max_chars // 2:
        cut = text.rfind(" ", 0, max_chars)
    if cut < max_chars // 2:
        cut = max_chars
    # Never cut inside a **bold** span: a sentence boundary inside one would
    # leave a dangling opener in the clipped text. Back up to the span's
    # opening so the whole span survives or is dropped whole (a span that
    # starts too early to back up to keeps the cut — the renderer tolerates
    # the unmatched marker as literal text).
    for m in _BOLD_RE.finditer(text):
        if m.start() < cut < m.end():
            if m.start() >= max_chars // 2:
                cut = m.start()
            break
    # Never cut inside a markdown link / bare URL / HTML anchor: back up to
    # its start when the backed-up piece is still substantial, else advance
    # past it when it still fits in the window (a whole link beats a broken
    # one — the ellipsis then follows the link instead of splitting it).
    for regex in (_LINK_RE, _ANCHOR_RE):
        for m in regex.finditer(text):
            if m.start() < cut < m.end():
                if m.start() >= max_chars // 2:
                    cut = m.start()
                elif m.end() <= max_chars:
                    cut = m.end()
                break
    clipped = text[:cut].rstrip()
    # Never end mid-tag (<br, <b, <i...): back up to before the dangling '<'.
    lt = clipped.rfind("<")
    if lt != -1 and clipped.find(">", lt) == -1:
        clipped = clipped[:lt].rstrip()
    return clipped + "\u2026"


def _markdown_to_html(text: str) -> str:
    """Convert markdown emphasis markers to balanced Telegram HTML.

    Ordered so nothing clobbers:
      0. entity-escaped + raw HTML emphasis tags (``&lt;code&gt;`` /
         ``<code>``) → markdown markers — the same defense-in-depth as
         :func:`_normalize_llm_markup` for tags that survived to this
         stage (double-escaped model output, pre-escaped callers).
      1. block constructs: ``## / ###`` headers → ``<b>``, ``---`` → thin
         separator, ``1.`` lists → ``1)``, pipe tables → bullet blocks.
      2. inline code `` `X` `` → ``<code>X</code>`` — before bold/italic so
         backtick content (which may contain ``**`` or ``*``) is never
         re-processed.
      3. bold ``**X**`` / ``***X***`` → ``<b>X</b>``
      4. italic ``*X*`` / ``_X_`` → ``<i>X</i>``
    Each marker's INNER content is :func:`_escape_inner` canonicalized, and
    unmatched markers (a lone ``*``, an unclosed backtick) pass through as
    literal text. MUST run AFTER ``html.escape()`` on the LLM path so raw
    LLM-supplied HTML is never re-introduced.
    """
    text = _escaped_html_emphasis_to_markdown(text)
    text = _raw_html_emphasis_to_markdown(text)
    text = _markdown_blocks_to_html(text)
    out: list[str] = []
    pos = 0
    for m in _INLINE_CODE_RE.finditer(text):
        out.append(_bold_italic_to_html(text[pos : m.start()]))
        out.append(f"<code>{_escape_inner(m.group(1))}</code>")
        pos = m.end()
    out.append(_bold_italic_to_html(text[pos:]))
    return "".join(out)


# Backward-compatible alias: the old bold-only converter is a strict subset
# of the full markdown→HTML pass (bold markers convert identically).
_markdown_bold_to_html = _markdown_to_html
