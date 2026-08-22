"""Test wave 14 — chat_ai/_markup package deep coverage.

Contracts read from source (2026-08-22). Pure functions only: no Telegram,
no network. Covers the beautifier, markdown→HTML pipeline, table fallback
and scan-summary grounding line that moved into the package during the
de-godding split.
"""

from __future__ import annotations

from rugmunchbot.formatters import thin_sep
from rugmunchbot.handlers.chat_ai._markup import (
    MAX_ANSWER_CHARS,
    _beautify_answer,
    _bold_italic_to_html,
    _cap_answer,
    _collapse_leading_emoji,
    _escape_inner,
    _escaped_html_emphasis_to_markdown,
    _format_scan_summary,
    _is_section_header,
    _is_separator_line,
    _is_table_block,
    _last_sentence_boundary,
    _markdown_blocks_to_html,
    _markdown_line_to_html,
    _markdown_to_html,
    _normalize_header_casing,
    _normalize_llm_markup,
    _render_table_block,
    _split_table_row,
    _tables_to_lists,
    _unwrap_bold,
)

_SEP = thin_sep()

# ── beautifier primitives ────────────────────────────────────────────────────


def test_last_sentence_boundary_finds_boundary_before_limit() -> None:
    text = "One. Two. Three."
    # rfind(". ", 0, 10) -> index 8 ("Two."), boundary just past it is 10.
    assert _last_sentence_boundary(text, 10) == 10
    assert _last_sentence_boundary(text, 7) == 5  # only "One." fits inside 7


def test_last_sentence_boundary_none_and_edge() -> None:
    assert _last_sentence_boundary("no separators here", 10) == -1
    # Punctuation ending exactly at the window edge clamps to the limit.
    assert _last_sentence_boundary("Hi. X", 4) == 4


def test_section_header_rules() -> None:
    assert _is_section_header("📊 Market Read") is True
    assert _is_section_header("**🛡️ Security**") is True  # bold wrapper tolerated
    assert _is_section_header("💰 $0.0001 | 24h: +45%") is False  # pipe = data line
    assert _is_section_header("📈 MCap: $50,000,000") is False  # digit-heavy
    assert _is_section_header("plain text line") is False
    assert _is_section_header("📊 " + "x" * 90) is False  # too long = prose


def test_unwrap_bold_variants() -> None:
    assert _unwrap_bold("**Title**") == "Title"
    assert _unwrap_bold("***Title***") == "Title"
    assert _unwrap_bold("*item") == "item"
    assert _unwrap_bold("plain") == "plain"


def test_separator_line_detection() -> None:
    assert _is_separator_line("──────") is True
    assert _is_separator_line("---") is True
    assert _is_separator_line("___") is True
    assert _is_separator_line("--") is False
    assert _is_separator_line("- text -") is False


def test_collapse_leading_emoji_runs() -> None:
    assert _collapse_leading_emoji("📊📊📊 Market Read") == "📊 Market Read"
    assert _collapse_leading_emoji("📊 📊 Market Read") == "📊 Market Read"
    assert _collapse_leading_emoji("🛡️🛡️ Security") == "🛡️ Security"  # VS16 kept
    assert _collapse_leading_emoji("📊🛡️ Mixed") == "📊🛡️ Mixed"  # different emoji


def test_normalize_header_casing_modes() -> None:
    assert _normalize_header_casing("📊 market read") == "📊 Market read"
    assert _normalize_header_casing("📊 MARKET READ") == "📊 Market Read"
    assert _normalize_header_casing("TA AND RSI") == "TA and RSI"  # abbreviations survive
    assert _normalize_header_casing("Bitcoin Outlook") == "Bitcoin Outlook"  # mixed untouched


def test_beautify_normalizes_header_and_inserts_separator() -> None:
    out = _beautify_answer("📊 market read\nbody text")
    assert out == f"**📊 Market read**\n{_SEP}\nbody text"


def test_beautify_dedupes_model_separators_under_header() -> None:
    out = _beautify_answer("📊 H\n────\n────\nbody")
    assert out == f"**📊 H**\n{_SEP}\nbody"


def test_beautify_trims_outer_blank_lines() -> None:
    out = _beautify_answer("\n\njust text\n\n")
    assert out == "just text"


# ── markdown → HTML pipeline ─────────────────────────────────────────────────


def test_escape_inner_is_idempotent_on_pre_escaped_text() -> None:
    assert _escape_inner("a < b & c") == "a &lt; b &amp; c"
    assert _escape_inner("a &lt; b &amp; c") == "a &lt; b &amp; c"  # no double escape


def test_bold_italic_conversion_and_guards() -> None:
    assert _bold_italic_to_html("**go**") == "<b>go</b>"
    assert _bold_italic_to_html("*slant*") == "<i>slant</i>"
    assert _bold_italic_to_html("_snake_case_ stays") == "_snake_case_ stays"
    assert _bold_italic_to_html("_real ital_") == "<i>real ital</i>"
    assert _bold_italic_to_html("lone * star") == "lone * star"


def test_escaped_html_emphasis_back_to_markdown() -> None:
    assert _escaped_html_emphasis_to_markdown("&lt;b&gt;hi&lt;/b&gt;") == "**hi**"
    assert _escaped_html_emphasis_to_markdown("&lt;code&gt;x&lt;/code&gt;") == "`x`"


def test_markdown_line_conversions() -> None:
    assert _markdown_line_to_html("## Market Overview") == "<b>Market Overview</b>"
    assert _markdown_line_to_html("## **Whole Bold**") == "<b>Whole Bold</b>"  # no double bold
    assert _markdown_line_to_html("1. first") == "1) first"
    assert _markdown_line_to_html("────") == _SEP
    assert _markdown_line_to_html("plain line") == "plain line"


def test_markdown_blocks_skip_fences_and_convert_tables() -> None:
    doc = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
    assert _markdown_blocks_to_html(doc) == doc  # fences verbatim
    table = "| a | b |\n|---|---|\n| 1 | 2 |"
    assert _markdown_blocks_to_html(table) == "**a · b**\n• 1 · 2"


def test_cap_answer_passthrough_and_paragraph_trim() -> None:
    assert _cap_answer("short", max_chars=100) == "short"
    # Paragraph boundary only wins when it sits past half the window.
    trimmed = _cap_answer("x" * 30 + "\n\n" + "y" * 50, max_chars=40)
    assert trimmed == "x" * 30 + "…"


def test_cap_answer_never_splits_link_or_dangling_tag() -> None:
    text = "a" * 35 + "https://example.com/longpath" + " b"
    out = _cap_answer(text, max_chars=40)
    assert out == "a" * 35 + "…"  # backed up to before the URL
    # A CLOSED '<b>' bracket pair inside the window survives the cut (only
    # its content is clipped); a truly UNCLOSED '<' is removed.
    closed = _cap_answer("a" * 37 + "<b>x", max_chars=40)
    assert closed == "a" * 37 + "<b>…"
    unclosed = _cap_answer("a" * 38 + "<bx", max_chars=40)  # len 41 > cap
    assert unclosed == "a" * 38 + "…"
    assert "<" not in unclosed


def test_markdown_to_html_protects_inline_code() -> None:
    out = _markdown_to_html("`x **not bold** y` and **real**")
    assert "<code>x **not bold** y</code>" in out
    assert "<b>real</b>" in out
    assert out.count("<b>") == 1  # code-span asterisks never became bold


def test_normalize_llm_markup_defuses_raw_html() -> None:
    assert _normalize_llm_markup("<b>BTC:</b> up &amp; running") == "**BTC:** up & running"
    assert _normalize_llm_markup("&amp;lt;safe&amp;gt;") == "&lt;safe&gt;"  # single pass


# ── table fallback ───────────────────────────────────────────────────────────


def test_split_table_row_trims_and_drops_outer_pipes() -> None:
    assert _split_table_row("| a | b |") == ["a", "b"]
    assert _split_table_row("|a||b|") == ["a", "", "b"]


def test_is_table_block_signals() -> None:
    assert _is_table_block(["|a|"]) is False
    assert _is_table_block(["|a|", "|b|"]) is False  # 2 rows, no separator
    assert _is_table_block(["|a|", "|b|", "|c|"]) is True  # 3+ rows imply table
    # Separator regex needs >=2 dash columns — a lone "|---|" is NOT one.
    assert _is_table_block(["|a|", "|---|"]) is False
    assert _is_table_block(["|a|b|", "|---|---|"]) is True  # definitive signal


def test_render_table_block_bullets_and_bold_header() -> None:
    rows = ["| a | b |", "|---|---|", "| 1 | 2 |"]
    assert _render_table_block(rows) == ["**a · b**", "• 1 · 2"]
    # Rows with an empty cell are dropped entirely.
    assert _render_table_block([*rows[:2], "|  | x |"]) == ["**a · b**"]


def test_tables_to_lists_respects_fences() -> None:
    doc = "```\n| a |\n|---|\n| 1 |\n```\n|x|y|\n|---|---|\n|1|2|"
    out = _tables_to_lists(doc)
    assert "| a |" in out  # fenced table untouched
    assert "**x · y**" in out  # real table converted
    assert "• 1 · 2" in out


# ── scan summary grounding line ──────────────────────────────────────────────


def test_format_scan_summary_none_when_unscanned() -> None:
    assert _format_scan_summary("0xabc", {}) is None
    assert _format_scan_summary("0xabc", {"risk_score": None}) is None


def test_format_scan_summary_renders_real_flags() -> None:
    line = _format_scan_summary(
        "0xabc",
        {
            "risk_score": 72,
            "is_honeypot": True,
            "is_mintable": None,  # absent -> omitted
            "is_renounced": False,  # explicit False -> shown
            "liquidity_usd": 25000,
            "holders": 150,
        },
    )
    assert line == (
        "Context: token 0xabc scan shows "
        "risk_score=72, is_honeypot=True, is_renounced=False, "
        "liquidity=$25,000, holders=150"
    )


def test_max_answer_chars_guardrail_constant() -> None:
    assert MAX_ANSWER_CHARS == 14000
