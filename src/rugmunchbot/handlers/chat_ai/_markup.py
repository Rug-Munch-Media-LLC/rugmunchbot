"""Markdown/HTML formatters + text chunking for the free-text AI chat pipeline.

Extracted from ``chat_ai/_helpers.py`` during the de-godding pass: the v10
response beautifier (emoji section headers, thin separators, ``**bold**``
markers), the raw-HTML → markdown normalization (defense-in-depth before
``html.escape()``), the paragraph/sentence-aware chunker, the scanner-context
summary line, and the markdown table → bullet-list fallback. Pure functions —
no ``Update``/``Context`` state, no handler registration. ``_helpers.py``
re-imports everything it needs from here; behavior is byte-for-byte identical.
"""

from __future__ import annotations

import re
from html import escape as _html_escape, unescape as _html_unescape
from typing import Any

from app.domains.telegram.rugmunchbot.formatters import thin_sep
from app.domains.telegram.rugmunchbot.handlers.chat_ai._parts import _is_fence_line

# Telegram hard limit is 4096; leave headroom for escape expansion.
CHUNK_MAX_CHARS = 3800

# Answer length cap applied in ``_send_answer`` BEFORE ``split_in_parts``:
# a simple query must never turn into a 1000-word wall. 4600 chars ≈ 650-750
# words — room for a thorough deep analysis; answers that exceed Telegram's
# 4096 message limit are split across parts by ``split_in_parts``. Capped
# answers are trimmed at a paragraph/sentence boundary and end with an
# ellipsis so truncation is honest.
MAX_ANSWER_CHARS = 4600

# ══════════════════════════════════════════════
# v10 RESPONSE BEAUTIFIER (see _beautify_answer / _markdown_bold_to_html)
# Section emojis match the persona's RESPONSE FORMATTING block (prompts.py).
# A line that STARTS with one of these and is short is treated as a section
# header: normalized to **emoji Title**, with thin_sep() inserted beneath it.
# ══════════════════════════════════════════════
_SECTION_EMOJIS: frozenset[str] = frozenset(
    [
        "\U0001f4ca",  # 📊 statistics
        "\U0001f4c8",  # 📈 chart increasing
        "\U0001f4c9",  # 📉 chart decreasing
        "\U0001f50d",  # 🔍 magnifying glass
        "\U0001f6e1\ufe0f",  # 🛡️ shield
        "\U0001f40b",  # 🐋 whale
        "\U0001f464",  # 👤 bust
        "\U0001f4b0",  # 💰 money bag
        "\U0001f525",  # 🔥 fire
        "\U0001f6a8",  # 🚨 siren
        "\u2705",  # ✅ check
        "\u26a0\ufe0f",  # ⚠️ warning
        "\U0001f4ac",  # 💬 speech balloon
        "\U0001f4f0",  # 📰 newspaper
        "\U0001f465",  # 👥 people
        "\U0001f680",  # 🚀 rocket
    ]
)
_SECTION_HEADER_MAX = 80  # longer emoji-prefixed lines are content, not headers
_BOLD_RE = re.compile(r"\*{2,3}([^*\n]+?)\*{2,3}")

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

# ══════════════════════════════════════════════
# RAW HTML → MARKDOWN NORMALIZATION (defense-in-depth)
# Local models sometimes emit RAW HTML (<b>BTC:</b>, &amp;, &lt;code&gt;)
# instead of Telegram markdown — even when the system prompts forbid it.
# Normalize it to markdown BEFORE escaping so the later escape() +
# _markdown_to_html turn it into real, balanced HTML instead of
# literal garbage (or unbalanced tags that Telegram rejects).
# ══════════════════════════════════════════════
_HTML_ENTITY_RE = re.compile(r"&(?:lt|gt|amp|quot|#x27);")
_HTML_ENTITY_MAP: dict[str, str] = {
    "&lt;": "<",
    "&gt;": ">",
    "&amp;": "&",
    "&quot;": '"',
    "&#x27;": "'",
}
_RAW_HTML_TAG_RE = re.compile(r"</?(?:b|strong|i|em|code|pre)\b[^>]*>")
_RAW_HTML_TAG_TO_MARKDOWN: dict[str, str] = {
    "b": "**",
    "strong": "**",
    "i": "_",
    "em": "_",
    "code": "`",
    "pre": "`",
}


def _unescape_llm_entities(text: str) -> str:
    """Unescape HTML entities a model emitted when it "thought" in HTML.

    Single-pass so double-encoded entities degrade safely (``&amp;lt;``
    becomes ``&lt;``, never a second unescape round).
    """
    return _HTML_ENTITY_RE.sub(lambda m: _HTML_ENTITY_MAP[m.group(0)], text)


def _raw_html_emphasis_to_markdown(text: str) -> str:
    """Convert raw HTML emphasis tags to Telegram markdown markers.

    ``<b>``/``<strong>`` → ``**``, ``<i>``/``<em>`` → ``_``,
    ``<code>``/``<pre>`` → backticks. Tags outside the emphasis set
    (``<br>``, ``<img>``, …) are left untouched.
    """

    def _repl(m: re.Match[str]) -> str:
        tag = m.group(0).strip("<>/").split()[0].lower()
        return _RAW_HTML_TAG_TO_MARKDOWN.get(tag, m.group(0))

    return _RAW_HTML_TAG_RE.sub(_repl, text)


def _normalize_llm_markup(text: str) -> str:
    """Defense-in-depth: normalize raw HTML an LLM emitted into markdown.

    Runs on the RAW answer BEFORE ``html.escape()``: unescapes entities the
    model already escaped (``&amp;`` → ``&``), then converts raw emphasis
    tags to markdown (``<b>BTC:</b>`` → ``**BTC:**``). The subsequent escape
    + :func:`_markdown_to_html` then produce real, balanced HTML.

    Fenced code blocks are NOT special-cased: a literal ``<b>`` inside a
    code block is converted too. This is a best-effort heuristic for a rare
    model failure mode — the system prompts forbid raw HTML outright.
    """
    return _raw_html_emphasis_to_markdown(_unescape_llm_entities(text))


# ══════════════════════════════════════════════
# SCANNER CONTEXT (address questions get a risk summary prepended)
# ══════════════════════════════════════════════


def _format_scan_summary(address: str, data: dict[str, Any]) -> str | None:
    """Compact single-line risk summary used to ground an address question.

    Returns ``None`` when the scanner returned no risk score so the caller
    falls through to a plain question.
    """
    risk = data.get("risk_score")
    if risk is None:
        return None
    flags = [f"risk_score={int(risk)}"]
    for key in ("is_honeypot", "is_mintable", "is_renounced"):
        val = data.get(key)
        if val is not None:
            flags.append(f"{key}={bool(val)}")
    liq = data.get("liquidity_usd") or 0
    if liq:
        flags.append(f"liquidity=${float(liq):,.0f}")
    holders = data.get("holders")
    if holders:
        flags.append(f"holders={holders}")
    return f"Context: token {address} scan shows {', '.join(flags)}"


def _last_sentence_boundary(text: str, limit: int) -> int:
    """Index just past the last sentence-ending punctuation before ``limit``.

    Returns -1 when no sentence boundary exists inside the window. Used by
    :func:`_beautify_answer` so answer caps land at sentence ends instead of
    mid-sentence ("split at natural section boundaries, not mid-sentence").
    """
    best = -1
    for sep_ in (". ", "? ", "! "):
        idx = text.rfind(sep_, 0, limit)
        if idx >= 0:
            best = max(best, idx + len(sep_))
    if best > limit:
        return limit  # boundary punctuation ends exactly at the window edge
    return best


# ══════════════════════════════════════════════
# v10 RESPONSE BEAUTIFIER
# Runs on the RAW LLM answer BEFORE chunking/escaping: normalizes emoji
# section headers, inserts thin separators under them, and keeps markdown
# **bold** markers for the post-escape conversion in _send_answer.
# ══════════════════════════════════════════════


def _is_section_header(line: str) -> bool:
    """True for a short line that begins with a section emoji (v10 header).

    A line starting with an emoji but longer than ``_SECTION_HEADER_MAX`` is
    prose, not a header — e.g. "📊 This token's chart broke out of a wedge
    that's been building since March" stays content. Prefix matching handles
    multi-code-point emojis (🛡️ = U+1F6E1 + VS16).

    What follows the emoji must be a TITLE, not data: a line containing a
    pipe (``💰 $0.0001 | 24h: +45%`` — a ``_format_tool_answer`` price line)
    or digit-heavy content (``📈 MCap: $50,000,000``) is a tool answer, never
    a header — only pure emoji + short non-numeric title lines are headers.
    """
    core = line.strip().lstrip("*").strip()
    if not core or len(core) > _SECTION_HEADER_MAX:
        return False
    for emoji in _SECTION_EMOJIS:
        if core.startswith(emoji):
            rest = core[len(emoji) :].strip()
            if "|" in rest:
                return False
            digits = sum(ch.isdigit() for ch in rest)
            letters = sum(ch.isalpha() for ch in rest)
            return digits <= letters
    return False


def _unwrap_bold(line: str) -> str:
    """Strip a balanced ``**...**``/``***...***`` wrapper (or leading `*`)."""
    stripped = line.strip()
    m = re.match(r"^\*{2,3}(.+?)\*{2,3}$", stripped)
    if m:
        return m.group(1).strip()
    return re.sub(r"^\*{1,3}", "", stripped).strip()


# Separator lines the model sometimes emits under a header (──── / ━━━━).
# Deduped against the thin_sep() the beautifier already inserts.
_SEPARATOR_LINE_RE = re.compile(r"^[\u2500\u2501\u2550\u2551\-_]{3,}$")
# Base emoji code points (excludes ZWJ so compound/role emoji are untouched);
# an optional VS16 is folded into the collapsed unit so 🛡️🛡️ → 🛡️ correctly.
_EMOJI_BASE = "\U0001f000-\U0001faff\u2600-\u27bf"
_LEADING_EMOJI_RUN_RE = re.compile(f"^([{_EMOJI_BASE}]\ufe0f?)(?:\\s*\\1)+")
# ALL-CAPS abbreviations that must survive title-casing (TA, RSI, MACD...).
_HEADER_ABBR = frozenset(
    {
        "TA",
        "RSI",
        "MACD",
        "EMA",
        "USD",
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "S/R",
        "DYOR",
        "AEGIS",
        "TVL",
        "ATH",
        "ATL",
        "HHI",
        "LP",
        "CEX",
        "DEX",
        "MEV",
        "AI",
        "NFT",
        "KOL",
        "P&L",
        "P/L",
        "GM",
        "GN",
        "X2",
    }
)
# Connector words in ALL-CAPS headers render lowercase ("TA and RSI").
_HEADER_CONNECTORS = frozenset({"AND", "OF", "THE", "FOR", "ON", "IN", "TO", "VS", "WITH"})


def _is_separator_line(line: str) -> bool:
    """True for a line that is only box-drawing dashes (model-emitted sep)."""
    return bool(_SEPARATOR_LINE_RE.match(line.strip()))


def _collapse_leading_emoji(text: str) -> str:
    """Collapse a run of identical emojis at the start of a header.

    ``📊📊📊 Market Read`` / ``📊 📊 Market Read`` → ``📊 Market Read``.
    The optional variation selector (VS16) is part of the collapsed unit so
    compound emoji like 🛡️ (U+1F6E1 + VS16) dedupe to 🛡️ — never 🛡.
    """
    return _LEADING_EMOJI_RUN_RE.sub(lambda m: m.group(1), text)


def _normalize_header_casing(text: str) -> str:
    """Best-effort header casing (v12): first word capitalized, ALL-CAPS
    prose title-cased, real abbreviations (TA/RSI/MACD/USD...) preserved.

    ``📊 market read`` → ``📊 Market read``; ``📊 MARKET READ`` →
    ``📊 Market Read``; ``📊 TA AND RSI`` → ``📊 TA and RSI``. Mixed-case
    headers are never rewritten, so proper nouns and $SYMBOLS survive.
    """
    words = text.split()
    if not words:
        return text
    # Leading pure-emoji tokens (📊 / 🛡️ / …) are not words — casing applies
    # to the first real word, and the emoji prefix is preserved verbatim.
    lead: list[str] = []
    while words and not any(ch.isalnum() for ch in words[0]):
        lead.append(words.pop(0))
    if not words:
        return text
    head = words[0]
    if head.islower() and head[0].isalpha():
        words[0] = head[0].upper() + head[1:]
    stripped = "".join(ch for ch in " ".join(words) if ch.isalpha())
    if len(stripped) >= 4 and stripped.isupper():
        words = [
            w.lower()
            if w in _HEADER_CONNECTORS
            else w
            if w in _HEADER_ABBR
            else (w[0].upper() + w[1:].lower())
            if w.isalpha()
            else w
            for w in words
        ]
    return " ".join(lead + words)


def _beautify_answer(answer: str) -> str:
    """v10 beautifier — normalize headers + insert thin separators (raw text).

    - Lines that look like section headers (start with a section emoji,
      short) are normalized to ``**emoji Title**`` on their own line, with a
      blank line before them so the chunker sees a clean section boundary.
    - ``thin_sep()`` is inserted beneath every header for visual rhythm
      (📊 header / ──── / content, like the /scan report blocks).
    - Model-emitted separator lines under a header are deduped (never two
      ``────`` in a row), repeated leading emojis collapse (no emoji spam),
      and header casing is normalized conservatively (v12).
    - All other lines are preserved verbatim (code blocks, lists, spacing).
    Markdown ``**bold**`` is left in place for :func:`_markdown_to_html`
    which runs AFTER escaping in :func:`_send_answer`.
    """
    lines = answer.split("\n")
    out: list[str] = []
    prev_empty = True
    for raw_line in lines:
        if not raw_line.strip():
            out.append("")
            prev_empty = True
            continue
        if _is_section_header(raw_line):
            if not prev_empty:
                out.append("")
            header_text = _collapse_leading_emoji(_unwrap_bold(raw_line))
            header_text = _normalize_header_casing(header_text)
            out.append(f"**{header_text}**")
            out.append(thin_sep())
            prev_empty = False
            continue
        if _is_separator_line(raw_line):
            # The model put a separator under a header we already decorated —
            # skip it instead of stacking a second dashed line.
            if out and _is_separator_line(out[-1]):
                continue
            out.append(raw_line)
            prev_empty = False
            continue
        out.append(raw_line)
        prev_empty = False
    start = 0
    while start < len(out) and not out[start]:
        start += 1
    out = out[start:]
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


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


# ══════════════════════════════════════════════
# MARKDOWN TABLE → LIST FALLBACK
# Telegram HTML has no table element; a raw markdown pipe table would render
# as a wall of literal ``|`` characters. Convert table blocks to compact
# bullet lists BEFORE escaping. Fenced code blocks are never touched.
# ══════════════════════════════════════════════
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


__all__ = [
    "CHUNK_MAX_CHARS",
    "MAX_ANSWER_CHARS",
    "_beautify_answer",
    "_cap_answer",
    "_format_scan_summary",
    "_is_section_header",
    "_markdown_bold_to_html",
    "_markdown_to_html",
    "_normalize_llm_markup",
    "_tables_to_lists",
    "_unescape_llm_entities",
]
