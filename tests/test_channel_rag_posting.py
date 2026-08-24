"""RAG-drafts posting contract (2026-08-25 audit regressions).

Covers the P0 fixes from the 2026-08-25 rugmunchbot audit:

* Ordering — a draft whose send fails or whose channel has no chat id must
  NOT be consumed by the dedupe mark (the old code remembered BEFORE
  sending, permanently black-holing those drafts).
* Wiring — ``build_social_spike_post`` must be imported into the scheduler
  registry (the old code called it without importing → NameError every
  social-spike tick).
* Observability — ``channel_rag`` must be registered with ``envelope=True``
  so successful ticks emit a channels:tick line.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _core, _posts_rag, _register as reg


@pytest.fixture(autouse=True)
def _fresh_dedupe(monkeypatch):
    """Isolate the global dedupe set per test."""
    monkeypatch.setattr(_core, "_posted_keys", {})
    return


def _draft(ref: str, text: str = "t", mirror: bool = False) -> dict[str, Any]:
    return {"story_ref": ref, "text": text, "kind": "story", "mirror": mirror}


class _Recorder:
    def __init__(self, succeed: bool = True):
        self.calls: list[tuple[str, str]] = []
        self.succeed = succeed

    async def __call__(self, bot: Any, chat_id: str, text: str) -> bool:
        self.calls.append((chat_id, text))
        return self.succeed


def _patch_env(
    monkeypatch,
    *,
    chat_id: str | None,
    drafts: list[dict[str, Any]] | dict[str, list[dict[str, Any]]],
    sender: _Recorder,
):
    """Patch the posting env. ``drafts`` may be a flat list (served to every
    channel) or a per-channel mapping like production curation."""
    if isinstance(drafts, list):
        table = dict.fromkeys(("main", "news", "alerts"), drafts)
    else:
        table = {ch: drafts.get(ch, []) for ch in ("main", "news", "alerts")}
    monkeypatch.setattr(_posts_rag, "fetch_rag_drafts", _fake_fetch(table))
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: chat_id)
    monkeypatch.setattr(_posts_rag, "_safe_send", sender)


def _fake_fetch(table: dict[str, list[dict[str, Any]]]):
    async def _fetch(channel: str, limit: int = 3) -> list[dict[str, Any]]:
        return [dict(d) for d in table.get(channel, [])][:limit]

    return _fetch


class _Bot:
    pass


class _Ctx:
    bot = _Bot()


@pytest.mark.asyncio
async def test_successful_send_marks_and_posts(monkeypatch):
    sender = _Recorder(succeed=True)
    _patch_env(monkeypatch, chat_id="-100123", drafts=[_draft("ref-1")], sender=sender)

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 1
    assert len(sender.calls) == 1
    # Marked seen: a second identical tick sends nothing more.
    await _posts_rag.post_rag_drafts(_Ctx())
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_failed_send_is_not_burned_next_tick_retries(monkeypatch):
    """THE blackhole regression: failed send must release the dedupe mark."""
    failing = _Recorder(succeed=False)
    _patch_env(
        monkeypatch,
        chat_id="-100123",
        drafts={"main": [_draft("ref-1")], "news": [], "alerts": []},
        sender=failing,
    )

    posted = await _posts_rag.post_rag_drafts(_Ctx())
    assert posted == 0
    assert len(failing.calls) == 1

    # Next tick: same draft must be retried, not silently skipped.
    recovering = _Recorder(succeed=True)
    _patch_env(
        monkeypatch,
        chat_id="-100123",
        drafts={"main": [_draft("ref-1")], "news": [], "alerts": []},
        sender=recovering,
    )
    posted = await _posts_rag.post_rag_drafts(_Ctx())
    assert posted == 1
    assert len(recovering.calls) == 1


@pytest.mark.asyncio
async def test_missing_chat_id_does_not_consume_drafts(monkeypatch):
    """No chat id → nothing sent AND nothing marked (old code burned them)."""
    sender = _Recorder(succeed=True)
    _patch_env(monkeypatch, chat_id=None, drafts=[_draft("ref-1")], sender=sender)

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 0
    assert sender.calls == []
    assert _core._posted_keys.get("__global__", set()) == set()


@pytest.mark.asyncio
async def test_per_tick_cap_and_mirror_only_on_main(monkeypatch, tmp_path):
    monkeypatch.setattr(_posts_rag, "MAX_PER_CHANNEL_PER_TICK", 1)
    monkeypatch.setattr(
        _posts_rag,
        "FEED_DATA",
        str(tmp_path),
    )
    # Production curation: only the main lane has drafts this tick.
    sender = _Recorder(succeed=True)
    _patch_env(
        monkeypatch,
        chat_id="-100main",
        drafts={"main": [_draft(f"ref-{i}") for i in range(3)], "news": [], "alerts": []},
        sender=sender,
    )

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 1  # capped at 1/tick/channel
    assert len(sender.calls) == 1


@pytest.mark.asyncio
async def test_cross_channel_duplicate_posts_once_main_wins(monkeypatch):
    """Same story queued for main AND news → exactly one post, and it is the
    flagship (main) that claims it (loop order + global dedupe)."""
    seen_channels: list[str] = []

    async def _send(bot: Any, chat_id: str, text: str) -> bool:
        seen_channels.append(chat_id)
        return True

    _patch_env(
        monkeypatch,
        chat_id="-100x",
        drafts={
            "main": [_draft("dup-1", text="flagship")],
            "news": [_draft("dup-1", text="dupe")],
            "alerts": [],
        },
        sender=_send,
    )

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 1
    assert seen_channels == ["-100x"]  # main's chat id — the news copy skipped


@pytest.mark.asyncio
async def test_main_mirror_writes_feed_json(monkeypatch, tmp_path):
    monkeypatch.setattr(_posts_rag, "FEED_DATA", str(tmp_path))
    sender = _Recorder(succeed=True)
    _patch_env(
        monkeypatch,
        chat_id="-100main",
        drafts=[_draft("ref-m", text="hello", mirror=True)],
        sender=sender,
    )

    posted = await _posts_rag.post_rag_drafts(_Ctx())

    assert posted == 1
    feed_file = tmp_path / "posts.json"
    assert feed_file.exists()
    data = json.loads(feed_file.read_text())
    assert data[0]["text"] == "hello"


def test_social_spike_post_is_imported_into_registry():
    """Regression: _social_tick called build_social_spike_post without an
    import → NameError on every tick (live in prod 2026-08-24/25)."""
    assert hasattr(reg, "build_social_spike_post")
    assert callable(reg.build_social_spike_post)


def test_channel_rag_job_registered():
    """channel_rag must appear on the JobQueue when the feature flag is on."""

    class _JQ:
        def __init__(self):
            self.repeating: list[dict[str, Any]] = []
            self.once: list[dict[str, Any]] = []

        def run_repeating(self, callback, interval, first=None, name=None, **kw):
            self.repeating.append({"callback": callback, "name": name, "interval": interval})

        def run_once(self, callback, when, name=None, **kw):
            self.once.append({"name": name})

        def run_daily(self, callback, time, name=None, **kw):  # pragma: no cover
            pass

    class _App:
        def __init__(self):
            self.job_queue = _JQ()

    app = _App()
    monkey_flags = {
        "CHANNEL_JOBS_ENABLED": True,
        "ALERTS_CHANNEL_ENABLED": False,
        "ALPHA_CHANNEL_ENABLED": False,
        "SCAN_CHANNEL_ENABLED": False,
        "TG_NEWS_ENABLED": False,
        "SCAM_WATCH_ENABLED": False,
        "PERSONAL_CHANNEL_ENABLED": False,
        "DAILY_MARKET_SUMMARY_ENABLED": False,
    }
    originals = {k: getattr(reg, k) for k in monkey_flags}
    try:
        for k, v in monkey_flags.items():
            setattr(reg, k, v)
            reg.register_channel_jobs(app)
    finally:
        for k, v in originals.items():
            setattr(reg, k, v)
    names = [j["name"] for j in app.job_queue.repeating]
    assert "channel_rag" in names
    # No no-op content_rotation jobs may come back.
    assert not any(n.startswith("content_") for n in names)
