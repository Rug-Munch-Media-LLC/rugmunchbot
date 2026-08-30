"""WO-3 universal format law (2026-08-25).

Covers /root/.knowledge/CONTENT-POSTING-SPEC.md:

* ``enforce_format_law`` — the mandatory pre-send pass: wall-of-text
  splitting, repeated-block dedupe, emoji-spam caps, ≤80-char bold
  headlines, mid-text truncation artifacts, HTML-tag preservation,
  idempotency.
* ``_safe_send`` wiring — every send passes through the law.
* ``_quality_gate`` — RAG drafts: template-fallback output is NEVER posted;
  drafts scoring under ``RMI_RAG_POST_MIN_SCORE`` are discarded.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.channels.formatters import enforce_format_law
from rugmunchbot.channels.formatters._format_law import (
    MAX_HEADLINE_CHARS,
    MAX_PARA_SENTENCES,
    MAX_SAME_EMOJI,
)
from rugmunchbot.channels.scheduler import _core, _posts_rag

# ── truncation artifacts ─────────────────────────────────────────────────


def test_mid_text_ellipsis_removed() -> None:
    text = "Bitcoin rallied hard… and then the market turned on a dime today."
    out = enforce_format_law(text)
    assert "…" not in out.replace("dime today.", "")


def test_trailing_ellipsis_clip_kept() -> None:
    text = "Whale wallet moved 12,000 ETH to exchanges…"
    assert enforce_format_law(text) == text


def test_trailing_whitespace_stripped() -> None:
    assert enforce_format_law("Headline   \n\nbody line.  ") == "Headline\n\nbody line."


# ── repeated blocks ──────────────────────────────────────────────────────


def test_repeated_paragraph_dropped() -> None:
    dup = "Top wallets control the supply and liquidity is thin."
    text = f"HEAD\n\n{dup}\n\n{dup}\n\nreal closing line."
    out = enforce_format_law(text)
    assert out.count(dup) == 1


def test_case_insensitive_dedupe() -> None:
    line = "Deployer history shows three prior rugged contracts."
    out = enforce_format_law(f"A\n\n{line}\n\n{line.upper()}\n")
    assert out.lower().count(line.lower()) == 1


def test_structural_short_lines_not_deduped() -> None:
    sep = "———"
    out = enforce_format_law(f"{sep}\nbody text here.\n{sep}")
    assert out.count(sep) == 2


def test_citation_label_line_dropped() -> None:
    text = "Real content stands alone.\nSource: Dictionary.com"
    out = enforce_format_law(text)
    assert "Dictionary.com" not in out
    assert "Real content stands alone." in out


# ── emoji spam ───────────────────────────────────────────────────────────


def test_consecutive_emoji_run_collapsed() -> None:
    out = enforce_format_law("🚨🚨🚨 Big rug confirmed on-chain.")
    assert out == "🚨 Big rug confirmed on-chain."


def test_same_emoji_capped_across_post() -> None:
    body = "\n".join(f"🚨 alert number {i} with details." for i in range(6))
    out = enforce_format_law(body)
    assert out.count("🚨") == MAX_SAME_EMOJI


# ── headline law ─────────────────────────────────────────────────────────


def test_overlong_bold_headline_clipped() -> None:
    long_title = " ".join(["word"] * 40)  # ~199 chars
    out = enforce_format_law(f"<b>{long_title}</b>")
    inner = out.removeprefix("<b>").removesuffix("</b>")
    assert len(inner) <= MAX_HEADLINE_CHARS
    assert inner.endswith("…")


def test_headline_with_link_label_clipped_href_kept() -> None:
    label = " ".join(["token"] * 40)
    href = "https://rugmunch.io/scanner?token=abc"
    out = enforce_format_law(f'<b><a href="{href}">{label}</a></b>')
    assert href in out  # URL survives
    assert "<a" in out  # tags intact
    assert "</a>" in out  # closing tag intact
    visible = out.split('">')[1].split("</a>")[0]
    assert len(visible) <= MAX_HEADLINE_CHARS


def test_inline_bold_not_treated_as_headline() -> None:
    # Bold inside a longer line (not a standalone line) must be untouched.
    line = f"Score: <b>{'x' * 120}</b> and more prose after."
    assert enforce_format_law(line) == line


# ── walls of text ────────────────────────────────────────────────────────


def _blob(sentences: int) -> str:
    return " ".join(f"Sentence number {i} says something substantive." for i in range(sentences))


def test_wall_of_text_split_into_max_sentence_paras() -> None:
    out = enforce_format_law(_blob(9))
    paras = [p for p in out.split("\n\n") if p.strip()]
    assert len(paras) == 3  # 9 sentences / 3 per para
    for para in paras:
        assert para.count(".") <= MAX_PARA_SENTENCES


def test_short_paragraph_left_alone() -> None:
    text = "One line. Two lines. Three lines."
    assert enforce_format_law(text) == text


def test_multiline_block_never_split() -> None:
    text = "Line one.\nLine two.\nLine three.\nLine four.\nLine five."
    assert enforce_format_law(text) == text


# ── HTML safety + idempotency ────────────────────────────────────────────


def test_html_tags_preserved() -> None:
    text = '<b>Title</b>\n\nBody with <a href="https://x.co">link</a> and <code>0xdead</code>.'
    out = enforce_format_law(text)
    assert '<a href="https://x.co">' in out
    assert "<code>0xdead</code>" in out
    assert "<b>Title</b>" in out


def test_idempotent() -> None:
    messy = "🚨🚨 <b>" + "word " * 30 + "</b>\n\n" + _blob(7) + "\n\n" + _blob(7)
    once = enforce_format_law(messy)
    assert enforce_format_law(once) == once


def test_empty_and_none_safe() -> None:
    # Contract: formatting only — blank input passes through unchanged.
    assert enforce_format_law("") == ""
    assert enforce_format_law("   ") == "   "


# ── _safe_send wiring ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_safe_send_applies_format_law(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class _Bot:
        async def send_message(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(_core, "_POST_RETRIES", 0)
    wall = _blob(9)
    await _core._safe_send(_Bot(), "-100", wall)
    sent = captured["text"]
    assert sent != wall  # the law ran
    assert sent.count("\n\n") >= 2  # split into paragraphs


# ── RAG quality gate ─────────────────────────────────────────────────────


def test_gate_blocks_template_writer_marker() -> None:
    assert _posts_rag._quality_gate({"writer": "template", "text": "anything"}) != ""


def test_gate_allows_llm_writer() -> None:
    assert _posts_rag._quality_gate({"writer": "llm", "text": "good post", "score": 0.8}) == ""


def test_gate_blocks_template_signature_heuristic() -> None:
    text = "[HIGH] Some token rugged\n\nbody here\n\nSource: telegram · https://x.co"
    assert _posts_rag._quality_gate({"text": text}) == "template_signature"


def test_gate_passes_normal_draft_without_writer() -> None:
    assert _posts_rag._quality_gate({"text": "A perfectly normal draft."}) == ""


def test_gate_blocks_story_template_variant() -> None:
    # 2026-08-30 feed incident: the rugbrain template writer emits a
    # "Story — Source: https://example.com/ref-1" headline-placeholder that
    # slipped past the [SEV]-only signature and posted to main + feed mirror.
    text = "Story — Source: https://example.com/ref-1"
    assert _posts_rag._quality_gate({"text": text}) != ""


def test_gate_allows_demo_source_url_fixture() -> None:
    # The shared test fixture uses "t — Source: https://example.com/x" as
    # generic (non-template) text — it must NOT be caught by the placeholder
    # gate (path /x, not /ref-N, no "Story —" prefix).
    assert _posts_rag._quality_gate({"text": "t — Source: https://example.com/x"}) == ""


def test_gate_blocks_below_threshold_score(monkeypatch) -> None:
    monkeypatch.setattr(_posts_rag, "RMI_RAG_POST_MIN_SCORE", 0.5)
    assert _posts_rag._quality_gate({"text": "ok", "score": 0.2}).startswith("below_threshold")


def test_gate_zero_or_missing_score_passes() -> None:
    # Sparse importance metadata must not block everything.
    assert _posts_rag._quality_gate({"text": "ok", "score": 0.0}) == ""
    assert _posts_rag._quality_gate({"text": "ok"}) == ""


def test_gate_unparsable_score_passes() -> None:
    assert _posts_rag._quality_gate({"text": "ok", "score": "high"}) == ""
