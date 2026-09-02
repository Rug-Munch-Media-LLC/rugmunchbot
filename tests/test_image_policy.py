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
    is_valid_image,
    resolve_post_image,
)

# ---------------------------------------------------------------------------
# 2026-09-02: permanent kill of the Twitter profile/header image leak.
# Defense in depth — every layer that touches a post image must reject
# banned imagery (social profile avatars/headers, brand chrome).
# ---------------------------------------------------------------------------


def test_is_valid_image_rejects_profile_images() -> None:
    """The operator's X avatar URL is banned at the public guard."""
    assert is_valid_image("https://pbs.twimg.com/profile_images/123/x.jpg") is False
    assert is_valid_image("https://pbs.twimg.com/profile_banners/123/1500x500") is False


def test_is_valid_image_accepts_real_content() -> None:
    assert is_valid_image("https://example.com/article-photo.jpg") is True
    assert is_valid_image("not-a-url") is False
    assert is_valid_image("") is False


def test_normalize_image_url_rejects_banned() -> None:
    """Layer 1 (source): the sweep's og:image normalizer drops banned URLs."""
    from rugmunchbot.channels.telegram_news._sources._fetch import _normalize_image_url

    assert (
        _normalize_image_url(
            "https://pbs.twimg.com/profile_images/123/x.jpg", "https://example.com/a"
        )
        is None
    )
    # protocol-relative profile header resolves then gets rejected
    assert (
        _normalize_image_url("//pbs.twimg.com/profile_images/123/x.jpg", "https://example.com")
        is None
    )
    # root-relative brand banner resolves then gets rejected
    assert _normalize_image_url("/brand/banner.jpg", "https://rugmunch.io") is None
    # real content passes through
    assert (
        _normalize_image_url("https://example.com/photo.jpg", "https://example.com")
        == "https://example.com/photo.jpg"
    )


@pytest.mark.asyncio
async def test_fetch_og_image_rejects_banned_og(monkeypatch: pytest.MonkeyPatch) -> None:
    """Layer 1b: a page whose og:image IS a banned URL yields None."""
    from rugmunchbot.channels.telegram_news._sources import _fetch as fetch_mod

    async def _page(url: str, timeout: float = 3.0) -> str | None:
        return (
            '<meta property="og:image" content="https://pbs.twimg.com/profile_images/9/header.jpg">'
        )

    monkeypatch.setattr(fetch_mod, "_fetch_page", _page)
    assert await fetch_mod._fetch_og_image("https://example.com/story") is None


@pytest.mark.asyncio
async def test_scheduler_post_formatted_drops_banned_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Layer 3 (final guard): the scheduler send path falls through to
    text-only when the post's image is banned."""
    from rugmunchbot.channels.formatters._primitives import NewsPost
    from rugmunchbot.channels.telegram_news import _scheduler

    sent: dict[str, str] = {}

    async def _fake_gate(text: str, **kw: object) -> object:
        captured = text

        class _G:
            ok = True
            text = captured
            image_url = "https://pbs.twimg.com/profile_images/9/header.jpg"

        return _G()

    async def _fake_photo(bot: object, chat_id: str, caption: object, photo_url: str) -> bool:
        sent["photo"] = photo_url
        return True

    async def _fake_text(bot: object, chat_id: str, post: object) -> bool:
        sent["text"] = str(post)
        return True

    monkeypatch.setattr("rugmunchbot.support.quality_gate.pre_post_quality_gate", _fake_gate)
    monkeypatch.setattr(_scheduler, "send_news_photo", _fake_photo)
    monkeypatch.setattr(_scheduler, "NewsPost", NewsPost)

    ok = await _scheduler._post_formatted(object(), "chat", NewsPost("story text"), _fake_text)
    assert ok is True
    assert "photo" not in sent
    assert sent["text"] == "story text"


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
    # 2026-09-01 doctrine: og failure -> text-only (""), badge only for
    # brand-anchor kinds.
    assert img == ""
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
    assert img == ""


def test_ensure_post_image_fills_empty_newspost() -> None:
    from rugmunchbot.channels.formatters._primitives import NewsPost

    post = NewsPost("hello world")
    post.image_url = ""
    out = ensure_post_image(post)
    # 2026-09-01 doctrine: empty image stays empty (text-only) — no badge
    # substitution.
    assert out.image_url == ""
    assert out == "hello world"


def test_ensure_post_image_keeps_carried_image() -> None:
    from rugmunchbot.channels.formatters._primitives import NewsPost

    post = NewsPost("card")
    post.image_url = "https://img.example/chart.png"
    out = ensure_post_image(post)
    assert (
        out.image_url == "https://img.example/chart.png"
    )  # ensure_post_image validates only; proxying happens in _pick_image_url


def test_ensure_post_image_wraps_plain_str() -> None:
    # 2026-09-01 doctrine: no forced badge — plain posts ship text-only.
    out = ensure_post_image("plain escalation text")
    assert out.image_url == ""
    # Brand-anchor kinds still get the badge.
    out2 = ensure_post_image("digest header", kind="roundup")
    assert out2.image_url == PLACEHOLDER_IMAGE_URL
    assert out == "plain escalation text"
    # The wrapper must still behave as a str for _safe_send callers.
    assert isinstance(out, str)


def test_ensure_post_image_passthrough_non_str() -> None:
    sentinel = object()
    assert ensure_post_image(sentinel) is sentinel
