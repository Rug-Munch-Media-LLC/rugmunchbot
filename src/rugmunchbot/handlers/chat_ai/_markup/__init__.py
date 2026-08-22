"""Markdown/HTML formatters + text chunking for the free-text AI chat pipeline.

Modularized from the former single-module ``chat_ai/_markup.py`` (2026-08-22).
This ``__init__`` is a pure re-export shim: every public name and signature is
unchanged, and callers keep importing ``chat_ai._markup`` exactly as before.

Extracted from ``chat_ai/_helpers.py`` during the de-godding pass: the v10
response beautifier (emoji section headers, thin separators, ``**bold**``
markers), the raw-HTML → markdown normalization (defense-in-depth before
``html.escape()``), the paragraph/sentence-aware chunker, the scanner-context
summary line, and the markdown table → bullet-list fallback. Pure functions —
no ``Update``/``Context`` state, no handler registration. ``_helpers.py``
re-imports everything it needs from here; behavior is byte-for-byte identical.

Layout:
  * ``_constants.py``    — chunk/caps limits, section emojis, bold regex
  * ``_normalize.py``    — raw HTML → markdown defense-in-depth normalization
  * ``_beautify.py``     — v10 beautifier (headers, separators, casing)
  * ``_html.py``         — markdown → Telegram HTML + answer capping
  * ``_tables.py``       — markdown pipe tables → bullet lists
  * ``_scan_summary.py`` — scanner-context grounding line for addresses
"""

from __future__ import annotations

from rugmunchbot.handlers.chat_ai._markup._beautify import (
    _beautify_answer as _beautify_answer,
    _collapse_leading_emoji as _collapse_leading_emoji,
    _is_section_header as _is_section_header,
    _is_separator_line as _is_separator_line,
    _last_sentence_boundary as _last_sentence_boundary,
    _normalize_header_casing as _normalize_header_casing,
    _unwrap_bold as _unwrap_bold,
)
from rugmunchbot.handlers.chat_ai._markup._constants import (
    CHUNK_MAX_CHARS as CHUNK_MAX_CHARS,
    MAX_ANSWER_CHARS as MAX_ANSWER_CHARS,
)
from rugmunchbot.handlers.chat_ai._markup._html import (
    _bold_italic_to_html as _bold_italic_to_html,
    _cap_answer as _cap_answer,
    _escape_inner as _escape_inner,
    _escaped_html_emphasis_to_markdown as _escaped_html_emphasis_to_markdown,
    _markdown_blocks_to_html as _markdown_blocks_to_html,
    _markdown_bold_to_html as _markdown_bold_to_html,
    _markdown_line_to_html as _markdown_line_to_html,
    _markdown_to_html as _markdown_to_html,
)
from rugmunchbot.handlers.chat_ai._markup._normalize import (
    _normalize_llm_markup as _normalize_llm_markup,
    _raw_html_emphasis_to_markdown as _raw_html_emphasis_to_markdown,
    _unescape_llm_entities as _unescape_llm_entities,
)
from rugmunchbot.handlers.chat_ai._markup._scan_summary import (
    _format_scan_summary as _format_scan_summary,
)
from rugmunchbot.handlers.chat_ai._markup._tables import (
    _is_table_block as _is_table_block,
    _render_table_block as _render_table_block,
    _split_table_row as _split_table_row,
    _tables_to_lists as _tables_to_lists,
)

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
