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

    async def _fake_text(bot: object, chat_id: str, post: object, **kw: object) -> bool:
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

    # Pry fallback also fails open (no thumbnail) -> text-only.
    async def _pry_none(url: str, **kw: object) -> str | None:
        return None

    monkeypatch.setattr(src, "_fetch_og_image", _fail)
    monkeypatch.setattr("rugmunchbot.support.image_policy.extract_thumbnail_with_pry", _pry_none)
    img = await resolve_post_image("news", source_url="https://example.com/story")
    assert img == ""

# ── 2026-09-04: PryScraper thumbnail extraction (og:image from article pages) ──

class _FakeResponse:
    """Minimal httpx response stub carrying a JSON body."""

    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body

class _FakeClient:
    """httpx.AsyncClient stub that returns a canned Pry response."""

    def __init__(self, body: dict) -> None:
        self._body = body

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def post(self, url: str, json: dict, **kw: object) -> _FakeResponse:
        return _FakeResponse(self._body)

def _patch_pry(monkeypatch: pytest.MonkeyPatch, body: dict) -> None:
    """Route the Pry POST + Redis cache to a canned response (no network)."""
    import httpx

    def _no_redis() -> None:
        return None

    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis_async", _no_redis)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(body))

@pytest.mark.asyncio
async def test_extract_thumbnail_with_pry_og_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pry HTML with an og:image meta tag yields that image URL."""
    from rugmunchbot.support import image_policy

    _patch_pry(
        monkeypatch,
        {
            "data": {
                "html": (
                    '<html><head><meta property="og:image" '
                    'content="https://cdn.example.com/story.png"></head></html>'
                )
            }
        },
    )
    thumb = await image_policy.extract_thumbnail_with_pry("https://example.com/story")
    assert thumb == "https://cdn.example.com/story.png"

@pytest.mark.asyncio
async def test_extract_thumbnail_with_pry_twitter_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to twitter:image when og:image is absent."""
    from rugmunchbot.support import image_policy

    _patch_pry(
        monkeypatch,
        {
            "data": {
                "html": (
                    '<html><head><meta name="twitter:image" '
                    'content="https://cdn.example.com/tw.png"></head></html>'
                )
            }
        },
    )
    thumb = await image_policy.extract_thumbnail_with_pry("https://example.com/story")
    assert thumb == "https://cdn.example.com/tw.png"

@pytest.mark.asyncio
async def test_extract_thumbnail_with_pry_first_img(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falls back to the first <img> tag when no meta image exists."""
    from rugmunchbot.support import image_policy

    _patch_pry(
        monkeypatch,
        {
            "data": {
                "html": ('<html><body><img src="https://cdn.example.com/inline.jpg"></body></html>')
            }
        },
    )
    thumb = await image_policy.extract_thumbnail_with_pry("https://example.com/story")
    assert thumb == "https://cdn.example.com/inline.jpg"

@pytest.mark.asyncio
async def test_extract_thumbnail_with_pry_banned_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A banned image (social profile avatar) is rejected -> None."""
    from rugmunchbot.support import image_policy

    _patch_pry(
        monkeypatch,
        {
            "data": {
                "html": (
                    '<meta property="og:image" '
                    'content="https://pbs.twimg.com/profile_images/9/header.jpg">'
                )
            }
        },
    )
    thumb = await image_policy.extract_thumbnail_with_pry("https://example.com/story")
    assert thumb is None

@pytest.mark.asyncio
async def test_extract_thumbnail_with_pry_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pry outage / no HTML / no image all yield None (never raises)."""
    from rugmunchbot.support import image_policy

    # Pry HTTP error -> None.
    class _BoomResponse:
        def raise_for_status(self) -> None:
            raise RuntimeError("pry down")

        def json(self) -> dict:
            return {}

    class _BoomClient:
        async def __aenter__(self) -> _BoomClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def post(self, url: str, json: dict) -> _BoomResponse:
            return _BoomResponse()

    import httpx

    def _no_redis() -> None:
        return None

    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis_async", _no_redis)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _BoomClient())
    assert await image_policy.extract_thumbnail_with_pry("https://example.com/a") is None

    # No HTML -> None.
    _patch_pry(monkeypatch, {"data": {"html": ""}})
    assert await image_policy.extract_thumbnail_with_pry("https://example.com/b") is None

    # HTML with no image -> None.
    _patch_pry(monkeypatch, {"data": {"html": "<html><body>no images here</body></html>"}})
    assert await image_policy.extract_thumbnail_with_pry("https://example.com/c") is None

@pytest.mark.asyncio
async def test_news_kind_og_failure_uses_pry_thumbnail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the direct og:image fetch fails, Pry supplies a real thumbnail."""
    import rugmunchbot.channels.telegram_news._sources as src

    async def _fail(url: str, timeout: float = 6.0) -> str | None:
        raise RuntimeError("network down")

    async def _pry(url: str, **kw: object) -> str | None:
        return "https://cdn.example.com/pry-thumb.png"

    monkeypatch.setattr(src, "_fetch_og_image", _fail)
    monkeypatch.setattr("rugmunchbot.support.image_policy.extract_thumbnail_with_pry", _pry)
    img = await resolve_post_image("news", source_url="https://example.com/story")
    # Pry thumbnail is proxied through the backend thumb endpoint.
    assert "cdn.example.com%2Fpry-thumb.png" in img
    assert "/api/v1/media/thumb?url=" in img

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

# ── 2026-09-02: banned twitter header / brand chrome + placeholder doctrine ──

def test_banned_twitter_header_rejected() -> None:
    """X profile banners and avatars are identity chrome, never post
    content — always rejected at the public guard."""
    assert not is_valid_image("https://pbs.twimg.com/profile_banners/123/456")
    assert not is_valid_image("https://pbs.twimg.com/profile_images/123/456")
    assert not is_valid_image("https://example.com/brand/banner.jpg")

def test_valid_article_image_accepted() -> None:
    """Real article / news imagery passes the guard."""
    assert is_valid_image("https://example.com/article-image.jpg")
    assert is_valid_image("https://cdn.coindesk.com/story-image.png")

def test_placeholder_is_app_logo() -> None:
    """The placeholder is the RMI badge (rmi_badge_v4.png).

    2026-10-17 operator doctrine supersedes 2026-09-01/02: the real RMI
    badge IS the app logo. The banned imagery remains the operator's X
    avatar and the header banner — never those."""
    assert "rmi_badge_v4.png" in PLACEHOLDER_IMAGE_URL
    assert "avatar" not in PLACEHOLDER_IMAGE_URL
    assert "banner" not in PLACEHOLDER_IMAGE_URL

# ── 2026-10-17: per-post-type brand graphics (operator directive) ──────────

def test_brand_graphics_are_first_party_and_allowed() -> None:
    """Every per-type brand graphic is a first-party rugmunch.io asset and
    passes the public image guard (the /brand/ ban exempts the sanctioned
    placeholder path — brand graphics are first-party, not scraped chrome)."""
    from rugmunchbot.support.post_graphics import POST_TYPE_GRAPHICS, graphic_for

    for post_type in POST_TYPE_GRAPHICS:
        url = graphic_for(post_type)
        assert url.startswith("https://rugmunch.io/brand/"), post_type

def test_formatter_attaches_per_type_graphic() -> None:
    """A formatter with no carried image attaches its per-type brand graphic
    (the image doctrine: every post type carries a distinct graphic)."""
    from rugmunchbot.channels.formatters import format_scam_alert
    from rugmunchbot.support.post_graphics import graphic_for

    post = format_scam_alert({"severity": "high", "address": "0xabc", "scam_intel": "drainer"})
    assert post.image_url == graphic_for("scam_alert")

def test_carried_image_still_wins_over_graphic() -> None:
    """A real carried image (og:image) beats the per-type brand graphic."""
    from rugmunchbot.channels.formatters import format_breaking_post

    post = format_breaking_post(
        {"title": "Story", "url": "https://e.com/a", "image_url": "https://cdn.example.com/x.png"}
    )
    assert "cdn.example.com" in post.image_url
