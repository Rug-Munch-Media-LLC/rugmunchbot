"""Continuation of test_image_policy.py - split for the file-size ceiling."""

from __future__ import annotations

from test_image_policy import (
    PLACEHOLDER_IMAGE_URL,
)


def test_placeholder_is_app_logo() -> None:
    """The placeholder is the RMI badge (rmi_badge_v4.png).

    2026-10-17 operator doctrine supersedes 2026-09-01/02: the real RMI
    badge IS the app logo. The banned imagery remains the operator's X
    avatar and the header banner — never those."""
    assert "rmi_badge_v4.png" in PLACEHOLDER_IMAGE_URL
    assert "avatar" not in PLACEHOLDER_IMAGE_URL
    assert "banner" not in PLACEHOLDER_IMAGE_URL


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
