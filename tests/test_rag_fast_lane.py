"""Breaking-news fast lane contract (strategy v2, 2026-08-26).

The fast poster escalates ONLY critical+fresh+sourceable drafts to
@rmialerts on a short tick, sharing the "rag:" dedupe namespace with the
slow lane so a story can never post twice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _core, _posts_rag


@pytest.fixture(autouse=True)
def _fresh_dedupe(monkeypatch):
    monkeypatch.setattr(_core, "_posted_keys", {})
    return


class _Recorder:
    def __init__(self, succeed: bool = True):
        self.calls: list[tuple[str, str]] = []
        self.succeed = succeed

    async def __call__(self, bot: Any, chat_id: str, text: str, **kw: Any) -> bool:
        self.calls.append((chat_id, text))
        return self.succeed


class _PhotoRecorder:
    """Records photo-aware sends (image doctrine: posts ship with images)."""

    def __init__(self, succeed: bool = True):
        self.calls: list[tuple[str, str, str]] = []
        self.succeed = succeed

    async def __call__(self, bot: Any, chat_id: str, post: Any, safe_send: Any, **kw: Any) -> bool:
        self.calls.append((chat_id, str(post), getattr(post, "image_url", "")))
        return self.succeed


class _Bot:
    # The quality gate attaches a placeholder image to image-less drafts,
    # so delivery may route through the photo-aware sender.
    async def send_photo(self, *a, **k):
        return True

    async def send_message(self, *a, **k):
        return True


class _Ctx:
    bot = _Bot()


def _critical_draft(**overrides: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "story_ref": "story:abc123",
        "text": (
            "Critical exploit in progress: the protocol's vault was drained "
            "via a price-oracle desync and the attacker is bridging funds out "
            "right now. Source: https://rekt.news/now"
        ),
        "kind": "story",
        "mirror": False,
        "severity": "critical",
        "writer": "llm",
        "score": 0.8,
        "ts": datetime.now(UTC).isoformat(),
    }
    d.update(overrides)
    return d


@pytest.mark.asyncio
async def test_fast_lane_posts_critical_fresh(monkeypatch):
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100alerts")
    monkeypatch.setattr(
        _posts_rag,
        "fetch_rag_drafts",
        _fake([_critical_draft()]),
    )
    sender = _PhotoRecorder()
    monkeypatch.setattr(_posts_rag, "_post_photo_aware", sender)

    assert await _posts_rag.post_rag_fast_lane(_Ctx()) == 1
    assert len(sender.calls) == 1
    # 2026-09-01 doctrine: story-doc images are UNTRUSTED — the fast lane
    # ships text-only unless the article's own og:image resolves.
    # (The old "never a naked text post" rule is retired — no badge spam.)


@pytest.mark.asyncio
async def test_fast_lane_skips_non_critical(monkeypatch):
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100alerts")
    monkeypatch.setattr(
        _posts_rag,
        "fetch_rag_drafts",
        _fake([_critical_draft(severity="high")]),
    )
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)

    assert await _posts_rag.post_rag_fast_lane(_Ctx()) == 0
    assert sender.calls == []


@pytest.mark.asyncio
async def test_fast_lane_shares_dedupe_with_slow_lane(monkeypatch):
    """A story the fast lane delivered must not post again via slow lane."""
    draft = _critical_draft()
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100alerts")
    monkeypatch.setattr(_posts_rag, "fetch_rag_drafts", _fake([draft]))
    sender = _PhotoRecorder()
    monkeypatch.setattr(_posts_rag, "_post_photo_aware", sender)

    assert await _posts_rag.post_rag_fast_lane(_Ctx()) == 1

    # Slow lane sees the same story_ref → deduped to zero.
    assert await _posts_rag.post_rag_drafts(_Ctx()) == 0
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_fast_lane_rejects_stale_critical(monkeypatch):
    from datetime import timedelta

    old = _critical_draft(ts=(datetime.now(UTC) - timedelta(hours=12)).isoformat())
    sender = _Recorder()
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: "-100alerts")
    monkeypatch.setattr(_posts_rag, "fetch_rag_drafts", _fake([old]))
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)

    assert await _posts_rag.post_rag_fast_lane(_Ctx()) == 0
    assert sender.calls == []


def _fake(drafts: list[dict[str, Any]]):
    async def _fetch(channel: str, limit: int = 3) -> list[dict[str, Any]]:
        return [dict(d) for d in drafts][:limit]

    return _fetch
