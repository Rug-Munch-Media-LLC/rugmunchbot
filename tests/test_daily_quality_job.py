"""Daily quality job — budget-gated, deduped, mirrored (deliverable 3)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _core, _posts_daily as pd, _posts_rag


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(_core, "_posted_keys", {})
    pd._QUALITY_POSTED_TODAY.clear()
    return


@pytest.fixture(autouse=True)
def _isolate_feed_data(monkeypatch, tmp_path):
    """Redirect the feed mirror to a temp dir for EVERY test.

    The 2026-08-30 feed incident re-leaked a ``ref-1`` / ``example.com``
    fixture into the LIVE store (``/var/www/feed.rugmunch.io/data/posts.json``)
    because ``post_daily_quality`` mirrors every successful main post through
    ``mirror_post_to_feed`` and the posting tests did not stub it. ``FEED_DATA``
    is a module global read at call time by ``mirror_post_to_feed``; pinning it
    to a per-test temp dir makes it structurally impossible for a successful
    daily-quality post in this module to reach the live mirror.
    """
    monkeypatch.setattr(_posts_rag, "FEED_DATA", str(tmp_path))
    return


def _draft(ref: str, title: str = "Story") -> dict[str, Any]:
    return {
        "story_ref": ref,
        "title": title,
        "text": f"{title} — Source: https://example.com/{ref}",
        "kind": "story",
        "ts": datetime.now(UTC).isoformat(),
    }


async def _fake_rerank_high(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic rerank: every candidate scores above the 0.30 floor.

    The live deep-4B cross-encoder scores short placeholder fixtures low
    (often < 0.30); patching it keeps these tests offline + deterministic while
    still exercising the real gate/floor/selection code path.
    """
    for it in items:
        it["importance"] = 0.8
    return sorted(items, key=lambda i: i.get("importance", 0.0), reverse=True)


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
        "rugmunchbot.channels.quality_schedule.rerank_quality", _fake_rerank_high, raising=False
    )
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
        "rugmunchbot.channels.quality_schedule.rerank_quality", _fake_rerank_high, raising=False
    )
    monkeypatch.setattr(
        "rugmunchbot.channels.main_cap.main_cap", _Cap(admit=True, count=0), raising=False
    )

    assert await pd.post_daily_quality(_Ctx()) == 0
    assert await pd.post_daily_quality(_Ctx()) == 1
    assert pd._quality_posted_today() == 1


@pytest.mark.asyncio
async def test_fresh_drafts_falls_back_to_bot_sources_when_rag_empty(monkeypatch):
    """2026-08-30 unify: when the RAG lane is empty, `_fresh_main_drafts`
    (delegating to `gather_main_drafts`) WARNs and folds in the bot's own
    curated news — routed through the reranker so the 0.30 importance floor
    applies. Fail-open to [] when even that has nothing."""

    # RAG lane empty/disabled → returns [].
    async def _rag(channel, limit=None):
        return []

    # Bot-owned news source → two real, sourced headlines.
    async def _news(limit=5):
        return [
            {
                "title": "Bridge exploit drains $190M",
                "summary": "Cross-chain bridge compromised; ~$190M drained.",
                "url": "https://rekt.news/bridge-190m",
                "source": "rekt",
                "sentiment_label": "",
                "factual_rating": "",
            },
            {
                "title": "Low-value altcoin tape chatter",
                "summary": "A quiet day in a thinly-traded microcap.",
                "url": "https://example.com/low-signal",
                "source": "example",
                "sentiment_label": "",
                "factual_rating": "",
            },
        ]

    # Deterministic rerank of the FALLBACK: the bridge headline scores high
    # (0.62) and the low-signal one scores 0.05 — the floor must keep only the
    # high-signal one. (The old code returned both unscored and posted both.)
    async def _rerank(items):
        for it in items:
            it["importance"] = 0.62 if "rekt.news" in it["story_ref"] else 0.05
        return sorted(items, key=lambda i: i.get("importance", 0.0), reverse=True)

    monkeypatch.setattr("rugmunchbot.channels.scheduler._posts_rag.fetch_rag_drafts", _rag)
    monkeypatch.setattr("rugmunchbot.channels.content.fetch_trending_news", _news)
    monkeypatch.setattr("rugmunchbot.channels.scheduler._posts_rag.rerank_quality", _rerank)

    drafts = await pd._fresh_main_drafts()

    # Scoring-gap fix: the low-signal fallback headline (0.05) is gated by the
    # 0.30 floor; only the 0.62 bridge headline survives.
    assert len(drafts) == 1
    d = drafts[0]
    assert d["title"] == "Bridge exploit drains $190M"
    assert "https://rekt.news/bridge-190m" in d["text"]  # source-link gate passes
    assert d["ts"]  # recency gate passes (dated now)
    assert d["story_ref"] == "https://rekt.news/bridge-190m"
    assert d["importance"] == 0.62  # fallback now CARRIES a real score

    # And the RAG-highway is preferred when it does yield a fresh draft.
    async def _rag_populated(channel, limit=None):
        return [
            {
                "story_ref": "exploit-1",
                "title": "RAG pick",
                "text": "RAG story. Source: https://example.com/x",
                "kind": "story",
                "ts": datetime.now(UTC).isoformat(),
                "importance": 0.9,
            }
        ]

    monkeypatch.setattr(
        "rugmunchbot.channels.scheduler._posts_rag.fetch_rag_drafts", _rag_populated
    )
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
