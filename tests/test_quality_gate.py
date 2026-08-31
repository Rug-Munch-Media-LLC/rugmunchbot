"""Tests for the pre-post quality gate (support.quality_gate).

Oracle note (AI-TEST-ORACLE-POLICY): each test drives the real gate against
hand-built fixtures and asserts exact outcomes (blocked / autofixed /
regenerated / passed) — not tautological pass/fail smoke checks.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.support.content_lint import BLAND_MIN_WORDS
from rugmunchbot.support.quality_gate import (
    PLACEHOLDER_IMAGE_URL,
    pre_post_quality_gate,
    strip_duplicate_urls,
)

CLEAN_TEXT = (
    "Strategy bought 4,603 BTC for $369.7M, lifting holdings to 845,050 BTC. "
    "The buy lands two weeks after their last tranche and keeps the 2026 "
    "accumulation pace above 2025's average. Treasury math still works at "
    "these funding costs, barely."
)


class _Logger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def warning(self, msg: str, *args: Any) -> None:
        self.records.append(("warning", msg % args if args else msg))

    def info(self, msg: str, *args: Any) -> None:
        self.records.append(("info", msg % args if args else msg))


# ── strip_duplicate_urls ────────────────────────────────────────────────────


def test_strip_duplicate_urls_keeps_first_anchor() -> None:
    text = '<a href="https://x.com/a">first</a> then <a href="https://x.com/a">second</a>'
    out = strip_duplicate_urls(text)
    assert out.count("https://x.com/a") == 1
    assert "second" in out  # label kept, href dropped


def test_strip_duplicate_urls_bare_links() -> None:
    text = "Read https://x.com/a and https://x.com/a again."
    out = strip_duplicate_urls(text)
    assert out.count("https://x.com/a") == 1


def test_strip_duplicate_urls_distinct_urls_untouched() -> None:
    text = "https://x.com/a and https://x.com/b"
    assert strip_duplicate_urls(text) == text


# ── gate: clean post passes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clean_post_passes_through() -> None:
    res = await pre_post_quality_gate(
        CLEAN_TEXT, image_url="https://img.example/btc.png", logger=_Logger()
    )
    assert res.ok
    assert not res.blocked
    assert res.text == CLEAN_TEXT
    assert res.image_url == "https://img.example/btc.png"
    assert not res.auto_fixed
    assert not res.regenerated


# ── gate: missing image ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_image_blocked_when_placeholder_disallowed() -> None:
    res = await pre_post_quality_gate(CLEAN_TEXT, allow_placeholder_image=False, logger=_Logger())
    assert not res.ok
    assert res.blocked
    assert any(v.rule == "missing-image" for v in res.violations)


@pytest.mark.asyncio
async def test_missing_image_autofixed_with_placeholder() -> None:
    res = await pre_post_quality_gate(CLEAN_TEXT, logger=_Logger())
    assert res.ok
    assert res.auto_fixed
    assert res.image_url == PLACEHOLDER_IMAGE_URL


# ── gate: double link fixed ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_double_link_autofixed() -> None:
    text = (
        "BTC ripped through 112k as ETF flows hit records. "
        'Read <a href="https://x.com/a">the story</a> and '
        '<a href="https://x.com/a">the follow-up</a> — the tape says '
        "institutions are not waiting for a dip, and neither is the desk."
    )
    res = await pre_post_quality_gate(
        text, image_url="https://img.example/btc.png", logger=_Logger()
    )
    assert res.ok
    assert res.auto_fixed
    assert res.text.count("https://x.com/a") == 1
    assert "the follow-up" in res.text


# ── gate: bland commentary regenerated ──────────────────────────────────────


@pytest.mark.asyncio
async def test_bland_commentary_regenerated_once() -> None:
    calls: list[str] = []

    async def regen(text: str, violations: list[Any]) -> str:
        calls.append(text)
        return CLEAN_TEXT

    res = await pre_post_quality_gate(
        "BTC up. https://x.com/a",
        image_url="https://img.example/btc.png",
        regenerate=regen,
        logger=_Logger(),
    )
    assert res.ok
    assert res.regenerated
    assert len(calls) == 1  # exactly ONE retry
    assert res.text == CLEAN_TEXT


@pytest.mark.asyncio
async def test_regeneration_failure_blocks() -> None:
    async def regen(text: str, violations: list[Any]) -> str:
        return "BTC up. https://x.com/a"  # still bland

    res = await pre_post_quality_gate(
        "BTC up. https://x.com/a",
        image_url="https://img.example/btc.png",
        regenerate=regen,
        logger=_Logger(),
    )
    assert not res.ok
    assert res.blocked
    assert any(v.rule == "bland-commentary" for v in res.violations)


@pytest.mark.asyncio
async def test_regenerator_exception_blocks_not_crashes() -> None:
    async def regen(text: str, violations: list[Any]) -> str:
        raise RuntimeError("llm down")

    res = await pre_post_quality_gate(
        "BTC up. https://x.com/a",
        image_url="https://img.example/btc.png",
        regenerate=regen,
        logger=_Logger(),
    )
    assert not res.ok
    assert res.blocked


# ── gate: banned phrase blocks (not auto-fixable) ───────────────────────────


@pytest.mark.asyncio
async def test_banned_phrase_blocks_without_regen() -> None:
    text = CLEAN_TEXT + " Stay safe out there, degens."
    res = await pre_post_quality_gate(
        text, image_url="https://img.example/btc.png", logger=_Logger()
    )
    assert not res.ok
    assert res.blocked
    assert any(v.rule == "banned-phrase" for v in res.violations)


@pytest.mark.asyncio
async def test_banned_phrase_blocks_even_after_failed_regen() -> None:
    async def regen(text: str, violations: list[Any]) -> str:
        return text  # regen echoes the slop back

    res = await pre_post_quality_gate(
        CLEAN_TEXT + " Stay safe out there.",
        image_url="https://img.example/btc.png",
        regenerate=regen,
        logger=_Logger(),
    )
    assert not res.ok
    assert res.blocked


# ── gate: min_words override (terse alert formats) ──────────────────────────


@pytest.mark.asyncio
async def test_min_words_override_relaxes_bland_check() -> None:
    short = (
        "🔴 Rug alert: TokenX drained its liquidity pool and the deployer "
        "wallet is cashing out right now. https://x.com/a"
    )
    ok = await pre_post_quality_gate(
        short, image_url="https://img.example/x.png", min_words=8, logger=_Logger()
    )
    assert ok.ok
    strict = await pre_post_quality_gate(
        short, image_url="https://img.example/x.png", logger=_Logger()
    )
    assert not strict.ok
    assert any(v.rule == "bland-commentary" for v in strict.violations)


def test_default_min_words_is_canonical() -> None:
    assert BLAND_MIN_WORDS == 20
