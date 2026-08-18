"""Multi-part delivery pipeline: long answers → several complete parts.

Regression coverage for the multi-part answer fix: a ~9500-char structured
analysis must survive the exact ``_send_answer`` pre-processing chain
(beautify → cap → split → escape → markdown→HTML) as several complete,
balanced-HTML parts with full content integrity — no truncation, no
unclosed tags, no lost sections.
"""

from __future__ import annotations

import re
from html import escape, unescape

from rugmunchbot.handlers.chat_ai._markup import (
    CHUNK_MAX_CHARS,
    MAX_ANSWER_CHARS,
    _beautify_answer,
    _cap_answer,
    _markdown_to_html,
    _tables_to_lists,
)
from rugmunchbot.handlers.chat_ai._parts import split_in_parts
from rugmunchbot.handlers.chat_ai._quality import strip_think_blocks

# ── 1. Build a LONG answer: ~9000 chars of structured analysis (2.5 parts) ──
_lead = """📊 **Market Read**

Here's the market read: BTC is consolidating in a tight range while event risk builds into Jackson Hole. Thin liquidity means any surprise whips the tape — positioning is defensive.

**Key levels:** Support at $63.0K (recent range low), resistance at $65.5K. A break above $65.5K opens a run at $67K; failure at $63K targets $61.5K. Funding is slightly positive but not stretched — no squeeze fuel either way.
"""
_section = """
**Deep-dive {n}:** The 4H structure is coiling under the 50-EMA with declining volume — classic pre-expansion compression. RSI sits at 48, neutral. Watch the $65.5K close for confirmation; a rejection there risks a retest of $63K. ETH mirrors BTC structurally but weaker — ETH/BTC drifting lower all week, and $1,900 is the pivot. SOL outperforms on momentum; $175 is the level to hold.

**Event risk {n}:** CPI before the open — a beat above 2.8% YoY keeps the dollar bid, a miss below 2.5% risks a relief rally. FOMC minutes clarify whether the September cut is still live (market ~60%). Jackson Hole Thu-Sat: Powell Friday; a dovish surprise squeezes short-duration positioning hard.

**Liquidity read {n}:** Order books are thin into the event window — spreads are wide on majors and alts gap more easily. A 2x overnight move is plausible on any surprise, so position sizing should reflect the tail risk. Verdict: neutral-to-defensive into the events; the edge is on reaction, not direction — patience beats prediction here.
"""
long_answer = (
    _lead
    + "".join(_section.format(n=i) for i in range(1, 7))
    + "\n\nNot financial advice — always DYOR."
)

_TAG_RE = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*\b[^>]*>")


def _balanced_html(part: str) -> bool:
    """True when *part* has balanced, properly nested HTML tags."""
    stack: list[str] = []
    for m in _TAG_RE.finditer(part):
        name = m.group(0).lstrip("</").rstrip(">").strip().split()[0].lower()
        if m.group(0).startswith("</"):
            if not stack or stack[-1] != name:
                return False
            stack.pop()
        else:
            stack.append(name)
    return not stack


def _preprocess(answer: str) -> str:
    """The exact _send_answer pre-processing chain."""
    beautified = _beautify_answer(_tables_to_lists(strip_think_blocks(answer)))
    return _cap_answer(beautified)


def test_long_answer_survives_cap() -> None:
    raw = _preprocess(long_answer)
    assert raw == _preprocess(long_answer)
    assert len(raw) > 4600, "test answer must exceed the OLD 4600-char cap to prove the fix"
    assert len(raw) <= MAX_ANSWER_CHARS, "cap must not truncate a legit long answer"


def test_split_produces_multiple_complete_parts() -> None:
    raw = _preprocess(long_answer)
    chunks = split_in_parts(raw, max_chars=CHUNK_MAX_CHARS)
    assert len(chunks) >= 2, "expected multi-part split for a ~9500-char answer"
    assert all(len(c) <= CHUNK_MAX_CHARS for c in chunks), "a part exceeds the chunk cap!"


def test_every_part_is_balanced_html() -> None:
    raw = _preprocess(long_answer)
    chunks = split_in_parts(raw, max_chars=CHUNK_MAX_CHARS)
    for i, part in enumerate(chunks):
        html = _markdown_to_html(escape(part))
        assert _balanced_html(html), f"part {i}: unbalanced HTML tags"


def test_content_integrity_across_parts() -> None:
    raw = _preprocess(long_answer)
    chunks = split_in_parts(raw, max_chars=CHUNK_MAX_CHARS)
    joined = unescape("".join(_markdown_to_html(escape(c)) for c in chunks)).lower()
    for frag in (
        "here's the market read",
        "deep-dive 6",
        "event risk 6",
        "not financial advice",
        "always dyor",
    ):
        assert frag in joined, f"missing content fragment: {frag}"
