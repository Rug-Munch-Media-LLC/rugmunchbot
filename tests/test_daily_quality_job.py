"""Daily quality job — budget-gated, deduped, mirrored (deliverable 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _core, _posts_daily as pd


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(_core, "_posted_keys", {})
    pd._QUALITY_POSTED_TODAY.clear()
    return


def _draft(ref: str, title: str = "Story") -> dict[str, Any]:
    return {
        "story_ref": ref,
        "title": title,
        "text": f"{title} — Source: https://example.com/{ref}",
        "kind": "story",
        "ts": datetime.now(UTC).isoformat(),
    }


class _Bot:
    pass


class _Ctx:
    bot = _Bot()


@pytest.mark.asyncio
async def test_post_daily_quality_sends_and_mirrors(monkeypatch, tmp_path):
    sent: list[tuple[str, str]] = []
    mirrors: list[dict[str, Any]] = []

    async def _send(bot, chat_id, text):
        sent.append((chat_id, text))
        return True

    def _mirror(item):
        mirrors.append(item)
        return "slug-z"

    monkeypatch.setattr(pd, "_chat_id", lambda ch: "-100main" if ch == "main" else None)
    monkeypatch.setattr(pd, "_safe_send", _send)

    async def _fresh():
        return [_draft("ref-1")]

    monkeypatch.setattr(pd, "_fresh_main_drafts", _fresh)
    monkeypatch.setattr(pd, "mirror_post_to_feed", _mirror)
    monkeypatch.setattr(
        "rugmunchbot.channels.main_cap.main_cap",
        _Cap(admit=True, count=3),
        raising=False,
    )

    posted = await pd.post_daily_quality(_Ctx())

    assert posted == 1
    assert len(sent) == 1
    assert sent[0][0] == "-100main"
    assert mirrors
    assert mirrors[0]["kind"] == "quality"
    assert pd._quality_posted_today() == 1


@pytest.mark.asyncio
async def test_post_daily_quality_respects_budget_and_cap(monkeypatch):
    """Already at quality budget → no post; main cap full → no post."""
    sent: list[str] = []

    async def _send(bot, chat_id, text):
        sent.append(text)
        return True

    monkeypatch.setattr(pd, "_chat_id", lambda ch: "-100main" if ch == "main" else None)
    monkeypatch.setattr(pd, "_safe_send", _send)

    async def _fresh():
        return [_draft("ref-1")]

    monkeypatch.setattr(pd, "_fresh_main_drafts", _fresh)
    monkeypatch.setattr(
        "rugmunchbot.channels.main_cap.main_cap", _Cap(admit=False, count=10), raising=False
    )

    assert await pd.post_daily_quality(_Ctx()) == 0
    assert sent == []

    # Budget exhausted by the job's own counter.
    pd._QUALITY_POSTED_TODAY[datetime.now(UTC).strftime("%Y%m%d")] = pd.QUALITY_SLOTS_PER_DAY
    monkeypatch.setattr(
        "rugmunchbot.channels.main_cap.main_cap", _Cap(admit=True, count=2), raising=False
    )
    assert await pd.post_daily_quality(_Ctx()) == 0
    assert sent == []


@pytest.mark.asyncio
async def test_post_daily_quality_releases_mark_on_failed_send(monkeypatch):
    """Failed send releases the dedupe mark → next run retries."""
    fails: list[int] = [0]

    async def _send(bot, chat_id, text):
        fails[0] += 1
        return fails[0] > 1  # first fails, second succeeds

    monkeypatch.setattr(pd, "_chat_id", lambda ch: "-100main" if ch == "main" else None)
    monkeypatch.setattr(pd, "_safe_send", _send)

    async def _fresh():
        return [_draft("ref-1")]

    monkeypatch.setattr(pd, "_fresh_main_drafts", _fresh)
    monkeypatch.setattr(
        "rugmunchbot.channels.main_cap.main_cap", _Cap(admit=True, count=0), raising=False
    )

    assert await pd.post_daily_quality(_Ctx()) == 0
    assert await pd.post_daily_quality(_Ctx()) == 1
    assert pd._quality_posted_today() == 1


@pytest.mark.asyncio
async def test_fresh_drafts_falls_back_to_bot_sources_when_rag_empty(monkeypatch):
    """2026-08-29 'daily MAIN didn't fire' fix: when the RAG lane is empty,
    `_fresh_main_drafts` falls back to the bot's own curated news so the daily
    quality slot still fills. Fail-open to [] when even that has nothing."""

    # RAG lane empty/disabled → returns [].
    async def _rag(channel, limit=None):
        return []

    # Bot-owned news source → one real, sourced headline.
    async def _news(limit=5):
        return [
            {
                "title": "Bridge exploit drains $190M",
                "summary": "Cross-chain bridge compromised; ~$190M drained.",
                "url": "https://rekt.news/bridge-190m",
                "source": "rekt",
                "sentiment_label": "",
                "factual_rating": "",
            }
        ]

    monkeypatch.setattr("rugmunchbot.channels.content.fetch_rag_drafts", _rag)
    monkeypatch.setattr("rugmunchbot.channels.content.fetch_trending_news", _news)

    drafts = await pd._fresh_main_drafts()

    assert len(drafts) == 1
    d = drafts[0]
    assert d["title"] == "Bridge exploit drains $190M"
    assert "https://rekt.news/bridge-190m" in d["text"]  # source-link gate passes
    assert d["ts"]  # recency gate passes (dated now)
    assert d["story_ref"] == "https://rekt.news/bridge-190m"

    # And the RAG-highway is preferred when it does yield a fresh draft.
    async def _rag_populated(channel, limit=None):
        return [
            {
                "story_ref": "exploit-1",
                "title": "RAG pick",
                "text": "RAG story. Source: https://example.com/x",
                "kind": "story",
                "ts": datetime.now(UTC).isoformat(),
            }
        ]

    monkeypatch.setattr("rugmunchbot.channels.content.fetch_rag_drafts", _rag_populated)
    prefs = await pd._fresh_main_drafts()
    assert prefs
    assert prefs[0]["story_ref"] == "exploit-1"


class _Cap:
    """Minimal main_cap double for admission/record/count."""

    def __init__(self, admit: bool, count: int):
        self._admit = admit
        self._count = count
        self.cap = 10

    async def admission(self, importance=None, critical=False):
        return self._admit, self._count

    async def count(self):
        return self._count

    async def record(self):
        return None
