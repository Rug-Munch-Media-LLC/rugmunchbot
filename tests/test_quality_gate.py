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


# ── 2026-08-31 regression: canonical URL dedupe ─────────────────────────────


def test_strip_duplicate_urls_canonical_variants() -> None:
    """http/https, tracking params and trailing slashes are the SAME link."""
    text = (
        "Story https://example.com/post/1?utm_source=tg and again "
        "http://example.com/post/1/ and once more "
        "https://example.com/post/1?fbclid=abc."
    )
    out = strip_duplicate_urls(text)
    assert out.count("example.com/post/1") == 1


def test_lint_double_link_canonical_variants() -> None:
    from rugmunchbot.support.content_lint import lint_text

    text = (
        "ETF flows hit records again today. https://example.com/a?utm_source=x "
        "and the same story at http://example.com/a/ — the tape does not lie "
        "about institutional demand this quarter, and the desk knows it."
    )
    rules = [v.rule for v in lint_text(text)]
    assert "double-link" in rules


def test_placeholder_image_url_is_real_image_path() -> None:
    """The placeholder must NOT be the old /og/ SPA-fallback path that
    served text/html — Telegram rejected it and every placeholder post
    silently shipped text-only (2026-08-31 no-image incident)."""
    assert PLACEHOLDER_IMAGE_URL == "https://rugmunch.io/brand/banner.jpg"
    assert PLACEHOLDER_IMAGE_URL.startswith("https://")


# ── 2026-08-31 integration: full main-channel posting path ─────────────────


class _FakeBot:
    def __init__(self) -> None:
        self.photos: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    async def send_photo(self, **kwargs: Any) -> None:
        self.photos.append(kwargs)

    async def send_message(self, **kwargs: Any) -> None:
        self.messages.append(kwargs)


@pytest.mark.asyncio
async def test_main_channel_double_link_no_image_payload_is_fixed_before_send() -> None:
    """Full main-lane contract: draft with a double link (in canonical
    variants) and no image → gate → single link + placeholder image →
    delivered via the photo-aware sender, never as a naked text message."""
    from rugmunchbot.channels.scheduler._core import _post_photo_aware
    from rugmunchbot.support.image_policy import ensure_post_image

    draft = (
        "TokenX drained its LP overnight and the deployer wallet is bridging "
        "the proceeds through a mixer right now. Full trace: "
        "https://example.com/trace?utm_source=rugbrain — same trace mirrored "
        "at http://example.com/trace/ for the archive."
    )
    gate = await pre_post_quality_gate(
        draft,
        image_url="",
        source="integration",
        kind="rag:main",
        min_words=8,
        require_image=True,
        allow_placeholder_image=True,
        logger=_Logger(),
    )
    assert gate.ok, [str(v) for v in gate.violations]
    assert gate.auto_fixed
    # single link (canonical dedupe collapsed all three variants)
    assert gate.text.count("example.com/trace") == 1
    # image attached (placeholder doctrine)
    assert gate.image_url == PLACEHOLDER_IMAGE_URL

    bot = _FakeBot()
    post = ensure_post_image(gate.text)
    post.image_url = gate.image_url
    sent_calls: list[str] = []

    async def _safe_send(b: Any, chat: Any, text: Any) -> bool:
        sent_calls.append("text")
        return True

    ok = await _post_photo_aware(bot, "@cryptorugmuncher", post, _safe_send)
    assert ok
    assert not sent_calls, "post must ship photo-aware, never text-only"
    assert len(bot.photos) == 1
    assert bot.photos[0]["photo"] == PLACEHOLDER_IMAGE_URL
    assert bot.photos[0]["caption"] == gate.text
