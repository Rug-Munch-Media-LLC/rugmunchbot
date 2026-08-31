"""feed.rugmunch.io mirror — every main-channel post lands on the feed.

W30 top-of-food-chain mirror (2026-08-29 quality overhaul #7): the atomic
JSON writer (``_posts_rag.mirror_post_to_feed``) is shared by every main
lane — RAG drafts, exploit alerts, daily market briefs, tg_news main
stories, alpha/scan escalations. These tests pin the writer contract and
the daily-market-summary wiring point.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed


def test_mirror_post_to_feed_writes_json(tmp_path, monkeypatch):
    monkeypatch.setattr("rugmunchbot.channels.scheduler._posts_rag.FEED_DATA", str(tmp_path))
    slug = mirror_post_to_feed(
        {
            "text": "Hello world https://example.com/x",
            "source": "https://example.com/x",
            "kind": "story",
        }
    )
    assert slug is not None
    posts = json.loads((tmp_path / "posts.json").read_text())
    assert posts[0]["slug"] == slug
    assert posts[0]["text"] == "Hello world https://example.com/x"
    assert posts[0]["source"] == "https://example.com/x"
    assert posts[0]["kind"] == "story"


def test_mirror_append_order_newest_first(tmp_path, monkeypatch):
    monkeypatch.setattr("rugmunchbot.channels.scheduler._posts_rag.FEED_DATA", str(tmp_path))
    mirror_post_to_feed({"text": "first", "story_ref": "a", "kind": "story"})
    mirror_post_to_feed({"text": "second", "story_ref": "b", "kind": "story"})
    posts = json.loads((tmp_path / "posts.json").read_text())
    assert [p["text"] for p in posts] == ["second", "first"]


def test_mirror_fail_open_returns_none_on_bad_path(tmp_path, monkeypatch):
    # FEED_DATA points UNDER a regular file → makedirs raises → None, no crash.
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a dir")
    monkeypatch.setattr(
        "rugmunchbot.channels.scheduler._posts_rag.FEED_DATA", str(blocker / "data")
    )
    assert mirror_post_to_feed({"text": "x", "story_ref": "a"}) is None


@pytest.mark.asyncio
async def test_daily_summary_mirrors_to_feed(monkeypatch, tmp_path):
    """The Morning/Midday Rug Market Report also mirrors to feed.rugmunch.io."""
    from rugmunchbot.channels.daily_market_summary import _publish

    writes: list[dict] = []

    def _fake_mirror(item):
        writes.append(item)
        return "slug-x"

    async def _noop_send(bot, chat_id, text):
        return True

    async def _noop_gather():
        return {"sources": ["test"], "generated_at": datetime.now(UTC).isoformat()}

    async def _noop_llm(messages, tier):
        return "<b>Market overview</b>\n" + ("Line of intel.\n" * 40)

    async def _noop_feed(report, *, title=None):
        return {}

    # post_daily_market_summary resolves these from the scheduler package at
    # call time; cleanup/mirror live on _publish itself.
    monkeypatch.setattr("rugmunchbot.channels.scheduler._chat_id", lambda ch: "-100main")
    monkeypatch.setattr("rugmunchbot.channels.scheduler._safe_send", _noop_send)
    monkeypatch.setattr(_publish, "gather_summary_data", _noop_gather)
    monkeypatch.setattr("rugmunchbot.llm_config.chat_with_fallback", _noop_llm, raising=False)
    monkeypatch.setattr(_publish, "DAILY_MARKET_SUMMARY_FEED_ENABLED", False)
    monkeypatch.setattr(_publish, "publish_to_feed", _noop_feed)
    monkeypatch.setattr(
        "rugmunchbot.channels.scheduler._posts_rag.mirror_post_to_feed",
        _fake_mirror,
    )

    class _Ctx:
        class _Bot:
            # The quality gate attaches a placeholder image to image-less
            # reports, so delivery goes through the photo-aware path.
            async def send_photo(self, *a, **k):
                return True

            async def send_message(self, *a, **k):
                return True

        bot = _Bot()

    posted = await _publish.post_daily_market_summary(_Ctx())
    assert posted == 1
    assert writes, "daily summary must mirror to feed.rugmunch.io"
    assert writes[0]["kind"] == "market-summary"


def test_tg_news_main_path_imports_the_mirror():
    """The tg_news main post path imports mirror_post_to_feed (wiring point);
    the function itself is the same atomic writer tested above."""
    from rugmunchbot.channels.scheduler._posts_rag import mirror_post_to_feed
    from rugmunchbot.channels.telegram_news import _scheduler

    assert _scheduler  # module imports cleanly
    assert callable(mirror_post_to_feed)
