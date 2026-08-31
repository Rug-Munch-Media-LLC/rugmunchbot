"""Tests for the all-channel image doctrine (support.image_policy).

Oracle note (AI-TEST-ORACLE-POLICY): each test drives the real resolver /
wrapper against hand-built fixtures and asserts exact outcomes — carried
image wins, og:image is attempted for news kinds, everything else lands on
the branded placeholder, and NewsPost wrapping always yields an attachable
image for ``_post_photo_aware``.
"""

from __future__ import annotations

import pytest

from rugmunchbot.support.image_policy import (
    PLACEHOLDER_IMAGE_URL,
    ensure_post_image,
    resolve_post_image,
)


@pytest.mark.asyncio
async def test_carried_image_wins() -> None:
    """Carried images win — and are proxied through the backend thumb
    endpoint so Telegram always gets bytes even when the publisher
    hotlink-blocks (2026-09-01 always-real-bytes doctrine)."""
    img = await resolve_post_image("scans", image_url="https://img.example/x.png")
    assert "img.example%2Fx.png" in img
    assert "/api/v1/media/thumb?url=" in img


@pytest.mark.asyncio
async def test_non_news_kind_gets_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scanner/whale/digest kinds never hit the network — placeholder only."""
    called = False

    async def _boom(url: str, timeout: float = 6.0) -> str | None:
        nonlocal called
        called = True
        return "https://og.example/a.png"

    import rugmunchbot.channels.telegram_news._sources as src

    monkeypatch.setattr(src, "_fetch_og_image", _boom)
    img = await resolve_post_image("scans", source_url="https://example.com/a")
    assert img == PLACEHOLDER_IMAGE_URL
    assert called is False


@pytest.mark.asyncio
async def test_news_kind_fetches_og_image(monkeypatch: pytest.MonkeyPatch) -> None:
    import rugmunchbot.channels.telegram_news._sources as src

    async def _og(url: str, timeout: float = 6.0) -> str | None:
        return "https://og.example/story.png"

    monkeypatch.setattr(src, "_fetch_og_image", _og)
    img = await resolve_post_image("news", source_url="https://example.com/story")
    # og:image result is proxied through the backend thumb endpoint
    assert "og.example%2Fstory.png" in img
    assert "/api/v1/media/thumb?url=" in img


@pytest.mark.asyncio
async def test_news_kind_og_failure_falls_back_to_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rugmunchbot.channels.telegram_news._sources as src

    async def _fail(url: str, timeout: float = 6.0) -> str | None:
        raise RuntimeError("network down")

    monkeypatch.setattr(src, "_fetch_og_image", _fail)
    img = await resolve_post_image("news", source_url="https://example.com/story")
    assert img == PLACEHOLDER_IMAGE_URL


def test_ensure_post_image_fills_empty_newspost() -> None:
    from rugmunchbot.channels.formatters._primitives import NewsPost

    post = NewsPost("hello world")
    post.image_url = ""
    out = ensure_post_image(post)
    assert out.image_url == PLACEHOLDER_IMAGE_URL
    assert out == "hello world"


def test_ensure_post_image_keeps_carried_image() -> None:
    from rugmunchbot.channels.formatters._primitives import NewsPost

    post = NewsPost("card")
    post.image_url = "https://img.example/chart.png"
    out = ensure_post_image(post)
    assert out.image_url == "https://img.example/chart.png"


def test_ensure_post_image_wraps_plain_str() -> None:
    out = ensure_post_image("plain escalation text")
    assert out.image_url == PLACEHOLDER_IMAGE_URL
    assert out == "plain escalation text"
    # The wrapper must still behave as a str for _safe_send callers.
    assert isinstance(out, str)


def test_ensure_post_image_passthrough_non_str() -> None:
    sentinel = object()
    assert ensure_post_image(sentinel) is sentinel
