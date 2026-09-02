"""Tests for story-level dedup (support.quality_gate._strip_duplicate_stories).

Oracle note (AI-TEST-ORACLE-POLICY): each test drives the real dedup
function against hand-built fixtures and asserts exact line counts and
content — not tautological pass/fail smoke checks.

The dedup collapses bullets that describe the SAME story with different
URLs (e.g. 3 blog posts about one exploit) while preserving genuinely
distinct stories and non-bullet lines (headers, blank lines).
"""

from __future__ import annotations

from rugmunchbot.support.quality_gate import _strip_duplicate_stories


def test_same_story_different_urls_collapses() -> None:
    """3 bullets about the same exploit with different URLs → 1 bullet."""
    text = """• Two exploits hit Solana AMM Aquifer causing $2.5M loss (url1)
• Two major exploits hit Solana AMM Aquifer costing $2.5M (url2)
• Two exploits hit Solana AMM Aquifer and Flow EVM (url3)
• HackenProof audit contest opened (url4)"""
    result = _strip_duplicate_stories(text)
    lines = [line for line in result.splitlines() if line.strip()]
    assert len(lines) == 2  # first story + HackenProof
    assert "Aquifer" in lines[0]
    assert "HackenProof" in lines[1]


def test_different_stories_all_survive() -> None:
    """Different stories should all survive dedup."""
    text = """• Solana AMM Aquifer exploited for $2.5M (url1)
• BTC ETF inflows hit $100M led by Solana (url2)
• NEAR drops 6.1% on $238M volume (url3)"""
    result = _strip_duplicate_stories(text)
    lines = [line for line in result.splitlines() if line.strip()]
    assert len(lines) == 3


def test_non_bullet_lines_preserved() -> None:
    """Non-bullet lines (headers, blank lines) should be preserved."""
    text = """<b>━━━ MARKET OVERVIEW ━━━</b>

• BTC $58,234 -0.52% (url1)
• ETH $2,567 -1.94% (url1)

<b>━━━ NARRATIVE ━━━</b>"""
    result = _strip_duplicate_stories(text)
    assert "MARKET OVERVIEW" in result
    assert "NARRATIVE" in result


def test_empty_and_blank_input_unchanged() -> None:
    """Empty / whitespace-only input passes through untouched."""
    assert _strip_duplicate_stories("") == ""
    assert _strip_duplicate_stories("   \n  ") == "   \n  "


def test_short_bullets_never_collapsed() -> None:
    """Bullets with <3 significant words are never deduped (too little
    signal to call them the same story)."""
    text = """• BTC up (url1)
• BTC up (url2)
• ETH down (url3)"""
    result = _strip_duplicate_stories(text)
    lines = [line for line in result.splitlines() if line.strip()]
    assert len(lines) == 3
