"""Daily quality schedule — 10-post budget, dedup, rerank (2026-08-29).

Covers the channel-quality overhaul deliverable 3 contract:

* Budget — exactly 2 market slots + up to 8 quality slots (10/day); fewer
  candidates → unfilled slots rendered honestly; ``posted_today`` shrinks
  the remaining quality budget.
* No double-post — the same story_ref/URL can never occupy two slots
  (stable-key dedup), near-duplicate copy collapses via embed-dedup
  (qwen3-4B), and already-posted keys (``seen_keys``) are excluded.
* Rerank — best-first via the 4B cross-encoder when reachable; fail-open
  to the item's existing score when not (never invents content).

All model calls are monkeypatched — these tests are offline and fast.
"""

from __future__ import annotations

import pytest

from rugmunchbot.channels.quality_schedule import (
    EMBED_DEDUP_THRESHOLD,
    MAIN_POSTS_PER_DAY,
    MARKET_SLOTS_PER_DAY,
    QUALITY_SLOTS_PER_DAY,
    ScheduleEntry,
    _candidate_text,
    _stable_key,
    build_daily_quality_schedule,
    cosine_similarity,
    dedupe_by_embedding,
    render_schedule,
    rerank_quality,
)


def _item(ref: str, title: str = "Story", text: str | None = None) -> dict:
    return {
        "story_ref": ref,
        "title": title,
        "text": text or f"{title} body — Source: https://example.com/{ref}",
        "kind": "story",
    }


def test_budget_constants_are_10():
    assert MAIN_POSTS_PER_DAY == 10
    assert MARKET_SLOTS_PER_DAY == 2
    assert QUALITY_SLOTS_PER_DAY == 8


def test_stable_key_strips_lane_prefix_keeps_url():
    assert _stable_key({"story_ref": "rag:https://example.com/x"}) == "https://example.com/x"
    assert _stable_key({"url": "https://example.com/y"}) == "https://example.com/y"
    assert _stable_key({"title": "Fallback title"}) == "Fallback title"


def test_candidate_text_prefers_title_plus_body():
    item = _item("a", title="Headline", text="Body text. https://example.com/a")
    text = _candidate_text(item)
    assert "Headline" in text
    assert "Body text" in text
    assert "https://example.com/a" in text


def test_cosine_similarity_basic():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert cosine_similarity([], [1.0]) == 0.0
    assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


@pytest.mark.asyncio
async def test_build_schedule_full_day(monkeypatch):
    monkeypatch.setattr(
        "rugmunchbot.channels.quality_schedule.dedupe_by_embedding",
        lambda items, threshold=EMBED_DEDUP_THRESHOLD: (items, {}),
    )
    monkeypatch.setattr(
        "rugmunchbot.channels.quality_schedule.rerank_quality",
        lambda items: sorted(
            ({**i, "importance": 0.5} for i in items),
            key=lambda i: i["importance"],
            reverse=True,
        ),
    )
    items = [_item(f"ref-{i}", title=f"Story {i}") for i in range(12)]
    schedule = await build_daily_quality_schedule(items, run_embed_dedup=False, run_rerank=False)

    assert len(schedule) == MAIN_POSTS_PER_DAY
    kinds = [e.kind for e in schedule]
    assert kinds.count("market-morning") == 1
    assert kinds.count("market-midday") == 1
    assert kinds.count("quality") == QUALITY_SLOTS_PER_DAY
    # No unfilled slots when candidates are plentiful.
    assert not any("unfilled" in e.title for e in schedule)
    rendered = render_schedule(schedule)
    assert "DAILY MAIN SCHEDULE" in rendered
    assert "10 posts/day" in rendered


@pytest.mark.asyncio
async def test_key_dedup_same_story_never_twice(monkeypatch):
    monkeypatch.setattr(
        "rugmunchbot.channels.quality_schedule.dedupe_by_embedding",
        lambda items, threshold=EMBED_DEDUP_THRESHOLD: (items, {}),
    )
    monkeypatch.setattr(
        "rugmunchbot.channels.quality_schedule.rerank_quality",
        lambda items: items,
    )
    # Same story_ref pushed twice → the second copy must collapse.
    items = [
        _item("ref-a", title="First"),
        _item("ref-a", title="Duplicate copy"),
        _item("ref-b", title="Second"),
    ]
    schedule = await build_daily_quality_schedule(items, run_embed_dedup=False, run_rerank=False)

    titles = [e.title for e in schedule if e.kind == "quality" and e.item is not None]
    assert titles.count("Duplicate copy") == 0  # same story_ref dropped
    assert titles.count("First") == 1


@pytest.mark.asyncio
async def test_seen_keys_excluded_global_no_double_post(monkeypatch):
    monkeypatch.setattr(
        "rugmunchbot.channels.quality_schedule.dedupe_by_embedding",
        lambda items, threshold=EMBED_DEDUP_THRESHOLD: (items, {}),
    )
    monkeypatch.setattr(
        "rugmunchbot.channels.quality_schedule.rerank_quality",
        lambda items: items,
    )
    items = [_item("already-posted", title="Posted elsewhere today"), _item("fresh", title="Fresh")]
    schedule = await build_daily_quality_schedule(
        items, seen_keys={"already-posted"}, run_embed_dedup=False, run_rerank=False
    )
    titles = [e.title for e in schedule if e.kind == "quality" and e.item is not None]
    assert titles == ["Fresh"]


@pytest.mark.asyncio
async def test_embed_dedup_collapses_near_duplicates(monkeypatch):
    """Near-identical copy collapses to one entry with an honest note."""

    # Fixed vectors: item 1 and item 2 are near-identical (cosine ~1.0),
    # item 3 is unrelated — the REAL dedupe logic runs on these.
    async def _fake_embed(texts):
        return [
            [1.0, 0.0, 1.0, 0.0],
            [0.999, 0.0, 0.998, 0.001],  # ~same story as item 1
            [0.0, 1.0, 0.0, 1.0],  # unrelated
        ]

    monkeypatch.setattr("rugmunchbot.channels.quality_schedule._embed_texts", _fake_embed)
    items = [
        _item("story-1", title="Ethereum ETF sees record inflows"),
        _item("story-2", title="ETH ETF inflows hit all-time high"),
        _item("story-3", title="Bridge exploit drains $190M"),
    ]
    kept, notes = await dedupe_by_embedding(items)
    assert [i["story_ref"] for i in kept] == ["story-1", "story-3"]
    assert "story-2" in notes
    assert notes["story-2"].startswith("embed_similar_to:story-1")


@pytest.mark.asyncio
async def test_embed_dedup_fail_open_no_signal(monkeypatch):
    """Zero vectors (dead embedder) → every candidate kept."""

    async def _no_signal(texts):
        return [[0.0] * 4 for _ in texts]

    monkeypatch.setattr("rugmunchbot.channels.quality_schedule._embed_texts", _no_signal)
    items = [_item("a"), _item("b")]
    kept, notes = await dedupe_by_embedding(items)
    assert len(kept) == 2
    assert notes == {}


@pytest.mark.asyncio
async def test_rerank_fail_open_keeps_scores(monkeypatch):
    """Dead reranker → items keep their existing score, never invented."""
    from rugmunchbot.channels.quality_schedule import _DEFAULT_IMPORTANCE

    assert _DEFAULT_IMPORTANCE == 0.5
    import httpx

    class _Down:
        def __init__(self, *a, **kw): ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def post(self, *a, **kw):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "AsyncClient", _Down)
    items = [
        {**_item("a"), "score": 0.9},
        {**_item("b"), "score": 0.2},
    ]
    ranked = await rerank_quality(items)
    assert ranked[0]["story_ref"] == "a"  # keeps existing 0.9 ordering
    assert ranked[0]["importance"] == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_posted_today_reduces_quality_slots(monkeypatch):
    monkeypatch.setattr(
        "rugmunchbot.channels.quality_schedule.dedupe_by_embedding",
        lambda items, threshold=EMBED_DEDUP_THRESHOLD: (items, {}),
    )
    monkeypatch.setattr(
        "rugmunchbot.channels.quality_schedule.rerank_quality",
        lambda items: items,
    )
    items = [_item(f"ref-{i}") for i in range(12)]
    schedule = await build_daily_quality_schedule(
        items, posted_today=3, run_embed_dedup=False, run_rerank=False
    )
    quality = [e for e in schedule if e.kind == "quality" and e.item is not None]
    assert len(quality) == QUALITY_SLOTS_PER_DAY - 3  # 5 remaining
    unfilled = [e for e in schedule if e.kind == "quality" and e.item is None]
    assert len(unfilled) == 3  # budget visible


def test_schedule_entry_to_dict():
    entry = ScheduleEntry(
        slot_index=2, slot_time="08:00", kind="quality", title="T", source="rag", importance=0.4
    )
    d = entry.to_dict()
    assert d["slot"] == "02"
    assert d["time_utc"] == "08:00"
    assert d["importance"] == 0.4
