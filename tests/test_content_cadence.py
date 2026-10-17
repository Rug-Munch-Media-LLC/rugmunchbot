"""The 3-unique-posts/day contract + image-per-story-type (2026-10-17).

Operator doctrine: exactly THREE unique anchor posts per day on
@cryptorugmuncher and the feed (morning Daily Rug / midday market report /
evening wrap), each a different facet, no story in two of the three; PLUS
major news as breaking throughout the day, deduped against the anchor.
Every post type resolves to its correct brand image.

Oracle note (AI-TEST-ORACLE-POLICY): each test drives the real resolver /
real anchor contract and asserts exact outcomes — no tautological smoke
checks. Redis is faked; no live services.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.channels import _edition_dedup as dedup, report_anchor as anchor
from rugmunchbot.support.post_graphics import (
    BRAND_BASE_URL,
    POST_TYPE_GRAPHICS,
    graphic_for,
    graphic_for_type,
)

# ── (a) exactly the 3 report types fire daily ──────────────────────────────


def test_anchor_is_exactly_three_editions() -> None:
    """The anchor is exactly three editions — the operator's 3/day law."""
    assert anchor.ANCHOR_POSTS_PER_DAY == 3
    assert len(anchor.anchor_editions()) == 3


def test_anchor_editions_are_distinct_and_ordered() -> None:
    """The three editions are distinct, ordered, and match the dedup list."""
    editions = tuple(e.edition for e in anchor.anchor_editions())
    assert editions == ("morning", "midday", "evening")
    assert editions == dedup.EDITIONS


def test_anchor_post_types_are_distinct() -> None:
    """Each anchor edition carries a DISTINCT post type (no collapse)."""
    post_types = anchor.anchor_post_types()
    assert post_types == ("daily_rug", "midday_report", "evening_wrap")
    assert len(set(post_types)) == 3


def test_assert_anchor_contract_passes() -> None:
    """The contract guard passes on the real anchor (no drift)."""
    anchor.assert_anchor_contract()


def test_assert_anchor_contract_detects_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    """A collapsed anchor (two editions sharing a post type) fails loudly."""
    monkeypatch.setattr(
        anchor,
        "ANCHOR_EDITIONS",
        (("morning", "daily_rug"), ("midday", "daily_rug"), ("evening", "evening_wrap")),
    )
    with pytest.raises(AssertionError):
        anchor.assert_anchor_contract()


# ── (b) each story appears in at most one of the 3 ─────────────────────────


class _FakeRedis:
    """Minimal Redis fake for the edition posted-sets (no live Redis)."""

    def __init__(self) -> None:
        self.sets: dict[str, set[bytes]] = {}

    def exists(self, key: str) -> int:
        return int(bool(self.sets.get(key)))

    def smembers(self, key: str) -> set[bytes]:
        return set(self.sets.get(key, set()))

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    def _sadd(self, key: str, members: set[str]) -> None:
        self.sets.setdefault(key, set()).update(m.encode() for m in members)


class _FakePipeline:
    def __init__(self, client: _FakeRedis) -> None:
        self._client = client
        self._key = ""
        self._members: set[str] = set()

    def __enter__(self) -> _FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def sadd(self, key: str, *members: str) -> _FakePipeline:
        self._key, self._members = key, set(members)
        return self

    def expire(self, key: str, ttl: int) -> _FakePipeline:
        return self

    def execute(self) -> list[bool]:
        self._client._sadd(self._key, self._members)
        return [True, True]


def _story(url: str, title: str = "T") -> dict[str, Any]:
    return {"url": url, "title": title}


def test_story_appears_in_at_most_one_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    """A story marked by morning is excluded from midday AND evening."""
    fake = _FakeRedis()
    monkeypatch.setattr(dedup, "_client", lambda: fake)

    shared = _story("https://example.com/shared")
    dedup.mark_edition_stories("morning", [shared])

    # Midday consults morning → drops the shared story.
    assert dedup.exclude_prior_editions([shared], before="midday") == []
    # Evening consults morning + midday → still drops it.
    assert dedup.exclude_prior_editions([shared], before="evening") == []


def test_distinct_stories_survive_each_edition(monkeypatch: pytest.MonkeyPatch) -> None:
    """A story not yet used by an earlier edition survives the filter."""
    fake = _FakeRedis()
    monkeypatch.setattr(dedup, "_client", lambda: fake)

    dedup.mark_edition_stories("morning", [_story("https://example.com/a")])
    fresh = _story("https://example.com/b")
    assert dedup.exclude_prior_editions([fresh], before="midday") == [fresh]


def test_additive_lane_excludes_all_anchor_stories(monkeypatch: pytest.MonkeyPatch) -> None:
    """The additive (news) lane drops a story ANY anchor edition carried."""
    fake = _FakeRedis()
    monkeypatch.setattr(dedup, "_client", lambda: fake)

    carried = _story("https://example.com/carried")
    dedup.mark_edition_stories("evening", [carried])
    fresh = _story("https://example.com/fresh")

    kept = anchor.exclude_report_stories([carried, fresh])
    assert kept == [fresh]


def test_additive_lane_fails_open_on_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage never blocks the additive lane (fail-open)."""
    monkeypatch.setattr(dedup, "_client", lambda: None)
    stories = [_story("https://example.com/a"), _story("https://example.com/b")]
    assert anchor.exclude_report_stories(stories) == stories


# ── (c) every post type resolves to its correct image ──────────────────────


#: The operator's image-per-story-type contract (2026-10-17).
_OPERATOR_IMAGE_MAP: dict[str, str] = {
    "scam_alert": "rmi-scam-alert.jpeg",
    "breaking": "rmi-breaking-news.jpeg",
    "news": "rmi-breaking-news.jpeg",
    "whale": "rmi-whale-alert.jpeg",
    "market": "rmi-evening-market-wrap.jpeg",
    "evening_wrap": "rmi-evening-market-wrap.jpeg",
    "midday_report": "rmi-midday-market-report.jpeg",
    "daily_rug": "daily-rug-logo.jpeg",
    # 2026-10-17: risk/alpha no longer use the profile photo — a post graphic
    # is content, never identity chrome (the retired avatar.jpg defect).
    "scan": "rmi_badge_v5.png",
    "alpha": "rmi-whale-alert.jpeg",
    "shill": "rmi-scam-alert.jpeg",
}


@pytest.mark.parametrize(("post_type", "asset"), sorted(_OPERATOR_IMAGE_MAP.items()))
def test_post_type_resolves_to_operator_image(post_type: str, asset: str) -> None:
    """Every post type resolves to its operator-authored brand image."""
    assert graphic_for(post_type) == f"{BRAND_BASE_URL}/{asset}"


def test_operator_images_are_all_mapped() -> None:
    """Every operator image is wired to at least one post type."""
    mapped_assets = set(POST_TYPE_GRAPHICS.values())
    for asset in set(_OPERATOR_IMAGE_MAP.values()):
        assert asset in mapped_assets, f"{asset} is not mapped to any post type"


def test_graphic_for_type_uses_override_filename() -> None:
    """A per-report override filename resolves to the brand URL (not default)."""
    url = graphic_for_type("midday_report", "rmi-midday-market-report.jpeg")
    assert url == f"{BRAND_BASE_URL}/rmi-midday-market-report.jpeg"


def test_graphic_for_type_uses_override_url_verbatim() -> None:
    """A full-URL override is used verbatim (off-site asset)."""
    url = graphic_for_type("midday_report", "https://cdn.example.com/x.png")
    assert url == "https://cdn.example.com/x.png"


def test_graphic_for_type_falls_back_to_post_type_map() -> None:
    """An empty override falls back to the post-type mapping."""
    assert graphic_for_type("midday_report", "") == graphic_for("midday_report")


def test_report_graphics_are_not_the_brand_default() -> None:
    """The three reports never fall back to the generic brand default.

    Regression: the publish modules passed a FILENAME to ``graphic_for`` as
    if it were a post type, so every report silently shipped the default
    avatar. The reports must resolve to their own operator images.
    """
    from rugmunchbot.support.post_graphics import DEFAULT_GRAPHIC

    for post_type in anchor.anchor_post_types():
        assert graphic_for(post_type) != DEFAULT_GRAPHIC, post_type
