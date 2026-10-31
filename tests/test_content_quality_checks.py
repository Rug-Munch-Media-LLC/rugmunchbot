"""Continuation of test_quality_gate.py - split for the file-size ceiling."""

from __future__ import annotations

from test_quality_gate import (
    CLEAN_TEXT,
    PLACEHOLDER_IMAGE_URL,
    strip_duplicate_urls,
)


def test_strip_duplicate_urls_calls_story_dedup() -> None:
    """strip_duplicate_urls must ALSO collapse same-story bullets with
    different URLs (not just duplicate links)."""
    from rugmunchbot.support.quality_gate import _strip_duplicate_stories

    text = """• Two exploits hit Solana AMM Aquifer causing $2.5M loss (url1)
• Two major exploits hit Solana AMM Aquifer costing $2.5M (url2)
• Two exploits hit Solana AMM Aquifer and Flow EVM (url3)
• HackenProof audit contest opened (url4)"""
    out = strip_duplicate_urls(text)
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 2
    assert "Aquifer" in lines[0]
    assert "HackenProof" in lines[1]
    # The story dedup helper is the mechanism behind it.
    assert _strip_duplicate_stories(text) == out


def test_placeholder_is_app_logo() -> None:
    """The placeholder is the BRAND logo (rug-munch-logo.jpg) — never the
    operator's avatar, badge or header banner (2026-10-17: avatar.jpg was
    rejected because the profile photo kept appearing as post art)."""
    assert "rmi_badge_v4.png" in PLACEHOLDER_IMAGE_URL
    assert "avatar" not in PLACEHOLDER_IMAGE_URL
    assert "banner" not in PLACEHOLDER_IMAGE_URL


def test_slop_phrases_detected() -> None:
    """detect_slop reports the drafting-artifact / AI-slop phrases."""
    from rugmunchbot.support.content_lint import detect_slop

    text = (
        "Let's delve into the ever-evolving landscape of crypto. "
        "It's worth noting the paradigm shift. MASTHEAD DONE NOW THE BODY."
    )
    found = detect_slop(text)
    assert "delve into" in found
    assert "ever-evolving" in found
    assert "landscape" in found
    assert "it's worth noting" in found
    assert "paradigm shift" in found
    assert "MASTHEAD DONE" in found
    assert "NOW THE BODY" in found


def test_slop_clean_text_returns_empty() -> None:
    from rugmunchbot.support.content_lint import detect_slop

    assert detect_slop(CLEAN_TEXT) == []
    assert detect_slop("") == []
