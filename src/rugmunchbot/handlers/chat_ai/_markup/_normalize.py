"""chat_ai markup normalization — raw HTML an LLM emitted → markdown.

Defense-in-depth layer that runs on the RAW answer BEFORE ``html.escape()``.
Extracted verbatim from the former single-module ``chat_ai/_markup.py``.
"""

from __future__ import annotations

import re

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
