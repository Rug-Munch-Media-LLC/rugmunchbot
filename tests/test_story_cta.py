"""Contract tests for the canonical story CTA (every content lane → rugmunch.io)."""

from __future__ import annotations

import pytest

from rugmunchbot.channels._story_cta import (
    CTA_MARKER,
    SECTION_PAGES,
    attach_story_cta,
    attributed_url,
    cta_line,
    story_cta,
    story_slug,
)

REQUIRED_LANES = (
    "morning",
    "midday",
    "evening",
    "news",
    "scam",
    "shill",
    "smart_money",
    "whales",
    "predictions",
)


def test_every_lane_has_a_section_page() -> None:
    missing = [lane for lane in REQUIRED_LANES if lane not in SECTION_PAGES]
    assert not missing, f"lanes without a section page: {missing}"
    for url in SECTION_PAGES.values():
        assert url.startswith("https://rugmunch.io/"), url


@pytest.mark.parametrize("key", ["story_slug", "slug", "ghost_slug"])
def test_story_slug_reads_payload_keys(key: str) -> None:
    assert story_slug({key: "etf-flows-week-42"}) == "etf-flows-week-42"


@pytest.mark.parametrize(
    "bad",
    ["", None, "Headline With Caps", "../etc/passwd", "a" * 200, "no_underscores"],
)
def test_story_slug_rejects_non_slugs(bad: object) -> None:
    assert story_slug({"slug": bad}) == ""


@pytest.mark.parametrize("lane", REQUIRED_LANES)
def test_cta_uses_real_slug_when_present(lane: str) -> None:
    resolved = story_cta({"slug": "solana-mev-drain"}, lane)
    assert resolved["story_url"] == (
        "https://rugmunch.io/story/solana-mev-drain"
        "?utm_source=telegram&utm_medium=bot"
        f"&utm_campaign={lane}&utm_content=rugmunchbot&ref=rugmunchbot"
    )
    assert resolved["reason"] is None


@pytest.mark.parametrize("lane", REQUIRED_LANES)
def test_cta_falls_back_to_section_page_and_flags_null(lane: str) -> None:
    resolved = story_cta({}, lane)
    assert resolved["story_url"] is None
    assert resolved["reason"] == "slug_missing"
    assert resolved["url"].startswith(SECTION_PAGES[lane] + "?utm_source=telegram")
    assert "ref=rugmunchbot" in resolved["url"]


def test_invalid_slug_is_marked_and_never_linked() -> None:
    resolved = story_cta({"slug": "Not A Slug!"}, "news")
    assert resolved["story_url"] is None
    assert resolved["reason"] == "slug_invalid"
    assert "Not" not in resolved["url"]


def test_cta_line_is_html_escaped_and_labelled() -> None:
    body = cta_line({"slug": "x-y"}, "news")
    assert body.startswith('\U0001f4d6 <a href="https://rugmunch.io/story/x-y?')
    assert CTA_MARKER in body


def test_cta_line_degrades_label_without_slug() -> None:
    body = cta_line({}, "scam")
    assert CTA_MARKER not in body
    assert "More on this story" in body
    assert "scam-school" in body


def test_attach_is_idempotent() -> None:
    once = attach_story_cta("teaser body", {"slug": "abc-def"}, "news")
    twice = attach_story_cta(once, {"slug": "abc-def"}, "news")
    assert once == twice
    assert once.count(CTA_MARKER) == 1
    assert once.startswith("teaser body\n\n")


def test_attach_never_touches_empty_text() -> None:
    assert attach_story_cta("", {"slug": "a-b"}, "news") == ""
    assert attach_story_cta("   ", {}, "news") == "   "


def test_unknown_lane_without_slug_adds_nothing() -> None:
    assert attach_story_cta("body", {}, "not-a-lane") == "body"


def test_unknown_lane_keeps_a_real_story_link() -> None:
    body = attach_story_cta("body", {"slug": "a-b"}, "not-a-lane")
    assert "rugmunch.io/story/a-b" in body


def test_attributed_url_is_idempotent_and_keeps_query() -> None:
    url = "https://rugmunch.io/news?page=2"
    once = attributed_url(url, "news")
    assert "page=2" in once
    assert "utm_campaign=news" in once
    assert attributed_url(once, "news") == once


def test_attributed_url_passes_non_http_through() -> None:
    assert attributed_url("tg://resolve?domain=x", "news") == "tg://resolve?domain=x"
