"""Scheduler dedup durability + content-level title-hash dedup (2026-09-02).

Regression coverage for the "stories appearing more than once" incident:

* Cross-channel global dedup — a story marked on channel A must not
  re-post on channel B (the ``__global__`` namespace contract).
* Durable backstop — after the in-memory window evicts a key, the Redis
  durable check (``scheduler_state.has_posted``) still catches re-posts.
* Title-hash dedup — the same story from two different URLs with the same
  headline dedupes (content-level, not just URL-level).
* Fail-open — Redis down never blocks posting (falls through to memory).

All Redis interaction is mocked; no network in tests.
"""

from __future__ import annotations

import pytest

from rugmunchbot.channels import scheduler_state
from rugmunchbot.channels.scheduler import _core


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Fresh in-memory dedupe state + durable layer disabled per test."""
    _core._posted_keys.clear()
    monkeypatch.setattr(scheduler_state, "is_available", lambda: False)
    yield
    _core._posted_keys.clear()


def test_cross_channel_global_dedup():
    """A story posted on channel A must not re-post on channel B."""
    assert _core._remember("rag_news", "rag:https://x.example/story-1") is True
    assert _core._remember("rag_main", "rag:https://x.example/story-1") is False
    assert _core._remember("tgnews", "tgnews:https://x.example/story-1") is False


def test_durable_check_catches_evicted_key(monkeypatch):
    """After eviction past _MAX_POSTED, the durable Redis check still says no."""
    # Simulate Redis durability: the key was persisted before eviction.
    durable = {"https://x.example/old-story"}

    def fake_has_posted(key: str) -> bool:
        return key in durable

    monkeypatch.setattr(scheduler_state, "is_available", lambda: True)
    monkeypatch.setattr(scheduler_state, "has_posted", fake_has_posted)
    monkeypatch.setattr(scheduler_state, "spawn_persist", lambda *a, **k: None)

    # Fill the window past the cap so the old key is evicted from memory.
    for i in range(_core._MAX_POSTED + 10):
        _core._remember("rag_news", f"rag:https://x.example/fill-{i}")

    assert len(_core._posted_keys["__global__"]) <= _core._MAX_POSTED
    # Evicted from memory, but durably posted → must NOT re-post.
    assert _core._remember("rag_news", "rag:https://x.example/old-story") is False


def test_eviction_keeps_recent_keys(monkeypatch):
    """Eviction keeps the most recent half of the window (recent keys stay)."""
    monkeypatch.setattr(scheduler_state, "is_available", lambda: True)
    monkeypatch.setattr(scheduler_state, "spawn_persist", lambda *a, **k: None)
    newest = _core._MAX_POSTED + 9
    for i in range(newest + 1):
        _core._remember("rag_news", f"rag:https://x.example/fill-{i}")
    # The newest key is still in the (truncated) set → still deduped.
    assert _core._remember("rag_news", f"rag:https://x.example/fill-{newest}") is False


def test_title_hash_dedup_same_story_different_urls():
    """Same headline from two sources (different URLs) dedupes by title hash."""
    t1 = "Trump's Digital Gold pushes Bitcoin to new high!"
    t2 = "Trumps Digital Gold pushes Bitcoin to new high"
    assert _core._remember("rag_news", "rag:https://a.example/1", title=t1) is True
    assert _core._remember("rag_main", "rag:https://b.example/2", title=t2) is False


def test_title_hash_distinct_titles_post():
    """Genuinely different stories with different titles both post."""
    assert _core._remember("rag_news", "rag:https://a/1", title="Solana memecoin rugs") is True
    assert _core._remember("rag_news", "rag:https://b/2", title="SEC approves ETF") is True


def test_title_hash_none_when_no_title():
    """No title → URL-only dedup (fail-open, no crash)."""
    assert _core._remember("rag_news", "rag:https://a/1", title=None) is True
    assert _core._remember("rag_news", "rag:https://a/1", title=None) is False


def test_release_undoes_title_hash_too():
    """A failed send releases BOTH the URL key and the title hash."""
    assert _core._remember("rag_news", "rag:https://a/1", title="Big Hack Drains Vault") is True
    _core._release("rag_news", "rag:https://a/1", title="Big Hack Drains Vault")
    assert _core._remember("rag_news", "rag:https://a/1", title="Big Hack Drains Vault") is True


def test_has_posted_fail_open(monkeypatch):
    """Redis down → has_posted returns False (never raises, never blocks)."""
    monkeypatch.setattr(scheduler_state, "_sync_acquire", lambda: None)
    assert scheduler_state.has_posted("anything") is False


def test_has_posted_hit(monkeypatch):
    """A durable hit returns True via SISMEMBER on the global set."""

    class FakeRedis:
        def sismember(self, skey, member):
            assert skey == "bot:channels:posted:__global__"
            return member == "known-key"

    monkeypatch.setattr(scheduler_state, "_sync_acquire", lambda: FakeRedis())
    assert scheduler_state.has_posted("known-key") is True
    assert scheduler_state.has_posted("unknown-key") is False


def test_title_hash_pure():
    """Deterministic, punctuation/stopword-insensitive, None-safe."""
    h1 = _core._title_hash("The Bitcoin ETF is approved!")
    h2 = _core._title_hash("bitcoin etf approved")
    assert h1 == h2
    assert _core._title_hash("") is None
    assert _core._title_hash(None) is None
    assert _core._title_hash("!!!") is None
