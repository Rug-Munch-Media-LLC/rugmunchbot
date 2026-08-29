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
