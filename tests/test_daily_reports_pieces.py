"""The three daily pieces: graphic, dateline, and feed carrying both.

Oracle note: each test drives the real resolver / real feed writer and
asserts an exact stored outcome. The failures these lock down were live
defects, not hypotheticals:

* the Evening Wrap default graphic named an asset that does not exist
  (``rmi-evening-wrap.jpeg``) and the image policy rejected it, so the third
  report shipped text-only;
* the feed mirror truncated every report at the 3900-char Telegram cap and
  dropped the headline, so the website carried half a commentary with no title.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import rugmunchbot.channels.scheduler._posts_rag as _posts_rag
from rugmunchbot.channels._report_mirror import FEED_TEXT_MAX, mirror_report
from rugmunchbot.channels.evening_wrap import EVENING_WRAP_GRAPHIC
from rugmunchbot.channels.midday_market_report import MIDDAY_REPORT_GRAPHIC
from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed
from rugmunchbot.support.image_policy import is_valid_image
from rugmunchbot.support.post_graphics import POST_TYPE_GRAPHICS, graphic_for_type

if TYPE_CHECKING:
    from pathlib import Path

_CONTEXT = {
    "generated_at": datetime(2026, 10, 24, 12, 30, tzinfo=UTC).isoformat(),
    "sources": ["prices"],
    "macro_stories": [],
    "recap_intel": [],
    "alerts": [],
}


def _builders():
    from rugmunchbot.channels.daily_rug import build_messages as morning
    from rugmunchbot.channels.evening_wrap import build_messages as evening
    from rugmunchbot.channels.midday_market_report import build_messages as midday

    return (morning, midday, evening)


def test_every_report_graphic_is_a_mapped_servable_asset() -> None:
    """All three editions resolve to a policy-valid, mapped brand asset."""
    for post_type, override in (
        ("daily_rug", ""),
        ("midday_report", MIDDAY_REPORT_GRAPHIC),
        ("evening_wrap", EVENING_WRAP_GRAPHIC),
    ):
        url = graphic_for_type(post_type, override)
        assert is_valid_image(url) is True, (post_type, override, url)
        filename = url.rsplit("/", 1)[-1]
        assert filename in POST_TYPE_GRAPHICS.values(), (post_type, filename)


def test_report_dateline_carries_date_and_time() -> None:
    """Every edition's masthead line states the date plus a UTC data time."""
    for build in _builders():
        system = build(dict(_CONTEXT))[0]["content"]
        assert "data as of 12:30 UTC" in system
        assert "Saturday, October 24, 2026" in system


def test_feed_keeps_headline_and_full_commentary(tmp_path: Path) -> None:
    """The writer stores the headline, the image and text past 3900 chars."""
    body = "Commentary. " * 400  # 5200 chars — over the old Telegram cap
    slug = mirror_post_to_feed(
        {
            "text": body,
            "story_ref": "daily-pieces",
            "kind": "briefing",
            "title": "The Evening Wrap",
            "tags": ["briefing", "evening_wrap"],
            "image_url": "https://rugmunch.io/brand/rmi-evening-market-wrap.jpeg",
        },
        store_dir=str(tmp_path),
    )
    stored = json.loads((tmp_path / "posts.json").read_text())[0]
    assert slug
    assert stored["title"] == "The Evening Wrap"
    assert stored["tags"] == ["briefing", "evening_wrap"]
    assert stored["image"] == stored["image_url"]
    assert len(stored["text"]) > 3900


def test_feed_mirror_does_not_truncate_at_telegram_cap(tmp_path: Path, monkeypatch) -> None:
    """mirror_report ships the whole report and keeps the edition graphic."""
    monkeypatch.setattr(_posts_rag, "FEED_DATA", str(tmp_path))
    report = "<b>━━━ The Midday Market Report ━━━</b>\n" + ("Analysis line. " * 350)
    url = "https://rugmunch.io/brand/rmi-midday-market-report.jpeg"
    deep_link = asyncio.run(
        mirror_report(
            report,
            url,
            edition="Midday",
            story_ref="midday-proof",
            tags=["briefing", "midday-report"],
        )
    )
    stored = json.loads((tmp_path / "posts.json").read_text())[0]
    assert deep_link.startswith("https://rugmunch.io/blog/")
    assert stored["image"] == url
    assert len(stored["text"]) == min(len(report), FEED_TEXT_MAX) > 3900


def test_feed_mirror_is_idempotent_per_story_ref(tmp_path: Path) -> None:
    """The same commentary + ref written twice yields one feed entry."""
    item = {"text": "Same body.", "story_ref": "dup-ref", "kind": "briefing"}
    first = mirror_post_to_feed(dict(item), store_dir=str(tmp_path))
    second = mirror_post_to_feed(dict(item), store_dir=str(tmp_path))
    stored = json.loads((tmp_path / "posts.json").read_text())
    assert first == second
    assert len(stored) == 1
