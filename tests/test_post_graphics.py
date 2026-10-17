"""Tests for the per-post-type graphics policy (support.post_graphics).

Oracle note (AI-TEST-ORACLE-POLICY): each test drives the real resolver
against the real map and asserts exact outcomes — every post type resolves
to a distinct brand asset, an unknown type falls back to the brand default,
and the runtime override seam works. No tautological smoke checks.
"""

from __future__ import annotations

from rugmunchbot.support.post_graphics import (
    BRAND_BASE_URL,
    DEFAULT_GRAPHIC,
    POST_TYPE_GRAPHICS,
    graphic_for,
    register_graphic,
)


def test_every_mapped_type_resolves_to_a_brand_url() -> None:
    """Every post type in the map resolves to a full http(s) brand URL."""
    for post_type in POST_TYPE_GRAPHICS:
        url = graphic_for(post_type)
        assert url.startswith(("http://", "https://")), post_type
        assert url != "", post_type


def test_unknown_type_falls_back_to_brand_default() -> None:
    """An unmapped post type never returns "" — it gets the brand default."""
    assert graphic_for("no-such-post-type") == DEFAULT_GRAPHIC
    assert graphic_for("") == DEFAULT_GRAPHIC
    assert graphic_for(None) == DEFAULT_GRAPHIC  # type: ignore[arg-type]


def test_post_types_are_distinct_by_family() -> None:
    """The flagship, news, risk and product families each get their own asset.

    The operator directive is DISTINCT graphics per post type — the four
    families must not collapse onto one image.
    """
    daily = graphic_for("daily_rug")
    news = graphic_for("news")
    risk = graphic_for("scan")
    product = graphic_for("update")
    assert len({daily, news, risk, product}) == 4


def test_scam_alert_has_a_dedicated_graphic() -> None:
    """The scam-alert post type resolves to a real brand asset."""
    url = graphic_for("scam_alert")
    assert url.startswith(BRAND_BASE_URL)
    assert url.endswith((".png", ".jpg", ".jpeg"))


def test_register_graphic_overrides_a_type() -> None:
    """The configurable seam: register_graphic re-points a post type."""
    original = POST_TYPE_GRAPHICS.get("news")
    try:
        register_graphic("news", "operator-new-graphic.png")
        assert graphic_for("news") == f"{BRAND_BASE_URL}/operator-new-graphic.png"
        # A full URL is used verbatim (off-site asset).
        register_graphic("news", "https://cdn.example.com/x.png")
        assert graphic_for("news") == "https://cdn.example.com/x.png"
    finally:
        if original is not None:
            POST_TYPE_GRAPHICS["news"] = original


def test_register_graphic_ignores_empty_input() -> None:
    """An empty type or asset is a no-op (never corrupts the map)."""
    before = dict(POST_TYPE_GRAPHICS)
    register_graphic("", "x.png")
    register_graphic("news", "")
    assert POST_TYPE_GRAPHICS == before


def test_no_post_type_uses_identity_chrome() -> None:
    """A post graphic is content, never a profile photo or channel header.

    ``avatar.jpg`` / ``current_cryptorugmuncher.png`` are identity chrome —
    the retired fallback defect. No mapping may point at one.
    """
    identity = {
        "avatar.jpg",
        "feed-avatar.jpg",
        "feed-banner.jpg",
        "banner.jpg",
        "1500x500.jpeg",
        "current_cryptorugmuncher.png",
    }
    for post_type, asset in POST_TYPE_GRAPHICS.items():
        assert str(asset).rsplit("/", 1)[-1].lower() not in identity, post_type
