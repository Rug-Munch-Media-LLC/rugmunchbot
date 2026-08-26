"""@rmiupdates changelog digest contract (strategy v2, 2026-08-26).

Covers:

* Composition — SHIPPED section groups conventional commits and caps lines;
  pipeline-health + feed-mirror sections always render.
* Posting — daily dedupe (second fire same day = no-op), failed send
  releases the mark so a retry can deliver, missing chat id = clean skip.
* Registration — ``channel_updates`` appears on the JobQueue when enabled.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _core, _posts_updates as up


@pytest.fixture(autouse=True)
def _fresh_dedupe(monkeypatch):
    monkeypatch.setattr(_core, "_posted_keys", {})
    return


class _Recorder:
    def __init__(self, succeed: bool = True):
        self.calls: list[tuple[str, str]] = []
        self.succeed = succeed

    async def __call__(self, bot: Any, chat_id: str, text: str) -> bool:
        self.calls.append((chat_id, text))
        return self.succeed


class _Bot:
    pass


class _Ctx:
    bot = _Bot()


def test_compose_renders_all_sections(monkeypatch):
    monkeypatch.setattr(up, "REPO_DIRS", ["/srv/work/repos/rugmunchbot"])
    monkeypatch.setattr(
        up,
        "_git_commits_since",
        lambda repo, since: [
            "feat(rag): dual-tier embedder contracts",
            "fix(bot): recency gate on RAG drafts",
            "chore: deps",
        ],
        raising=False,
    )
    text = up.compose_updates_digest(health_lines=["✅ rag-api", "❌ embed-deep-4b"])
    assert "system progress" in text
    assert "Shipped (3 commits/24h)" in text
    assert "feat(rag): dual-tier embedder contracts" in text
    assert "✅ rag-api" in text
    assert "feed mirror" in text
    assert "Posting volume" in text


def test_posting_volume_maps_chat_ids_to_roles(monkeypatch):
    from rugmunchbot.channels.scheduler import _core

    _core._POST_COUNTERS.clear()
    _core._POST_COUNTERS["20260826"] = {"-100main": 3, "-100alerts": 1}
    monkeypatch.setattr(
        "rugmunchbot.config._resolve_channels",
        lambda: {"main": "-100main", "alerts": "-100alerts"},
        raising=False,
    )
    lines = up._posting_volume_lines()
    assert any("main: 3" in ln for ln in lines)
    assert any("alerts: 1" in ln for ln in lines)
    assert any("total: 4" in ln for ln in lines)
    _core._POST_COUNTERS.clear()


def test_render_shipped_caps_and_counts():
    subjects = [f"feat(x): commit {i}" for i in range(12)]
    lines = up._render_shipped(up._group_commits(subjects))
    # 8 commit lines + 1 overflow note.
    assert len(lines) == up._MAX_COMMIT_LINES + 1
    assert any("and 4 more" in line for line in lines)


def test_group_commits_buckets_by_type():
    groups = up._group_commits(
        ["feat(a): x", "fix(b): y", "perf(c): z", "security(d): w", "docs: q"]
    )
    assert len(groups["feat"]) == 1
    assert len(groups["fix"]) == 1
    assert len(groups["perf"]) == 1
    assert len(groups["security"]) == 1
    assert groups["other"] == ["docs: q"]


@pytest.mark.asyncio
async def test_post_skips_without_chat_id(monkeypatch):
    sender = _Recorder()
    monkeypatch.setattr(up, "_chat_id", lambda ch: None)
    monkeypatch.setattr(up, "_safe_send", sender)
    assert await up.post_updates_digest(_Ctx()) == 0
    assert sender.calls == []


@pytest.mark.asyncio
async def test_post_once_per_day_and_release_on_failure(monkeypatch):
    monkeypatch.setattr(up, "_chat_id", lambda ch: "-100upd")

    async def _no_probes() -> list[str]:
        return ["✅ rag-api"]

    monkeypatch.setattr(up, "_probe_services", _no_probes)
    failing = _Recorder(succeed=False)
    monkeypatch.setattr(up, "_safe_send", failing)

    assert await up.post_updates_digest(_Ctx()) == 0
    assert len(failing.calls) == 1

    # Same day, send now works → must deliver (mark was released).
    ok_sender = _Recorder(succeed=True)
    monkeypatch.setattr(up, "_safe_send", ok_sender)
    assert await up.post_updates_digest(_Ctx()) == 1
    assert len(ok_sender.calls) == 1

    # Second success same day → deduped to zero.
    assert await up.post_updates_digest(_Ctx()) == 0
    assert len(ok_sender.calls) == 1


def test_channel_updates_job_registered():
    class _JQ:
        def __init__(self):
            self.repeating: list[dict[str, Any]] = []
            self.daily: list[dict[str, Any]] = []

        def run_repeating(self, callback, interval, first=None, name=None, **kw):
            self.repeating.append({"name": name})

        def run_daily(self, callback, time, days=None, name=None, **kw):
            self.daily.append({"name": name})

        def run_once(self, callback, when, name=None, **kw):
            pass

    class _App:
        def __init__(self):
            self.job_queue = _JQ()

    from rugmunchbot.channels.scheduler import _register as reg

    app = _App()
    flags = {
        "CHANNEL_JOBS_ENABLED": True,
        "ALERTS_CHANNEL_ENABLED": False,
        "ALPHA_CHANNEL_ENABLED": False,
        "SCAN_CHANNEL_ENABLED": False,
        "TG_NEWS_ENABLED": False,
        "SCAM_WATCH_ENABLED": False,
        "PERSONAL_CHANNEL_ENABLED": False,
        "DAILY_MARKET_SUMMARY_ENABLED": False,
        "UPDATES_CHANNEL_ENABLED": True,
    }
    originals = {k: getattr(reg, k) for k in flags}
    try:
        for k, v in flags.items():
            setattr(reg, k, v)
        reg.register_channel_jobs(app)
    finally:
        for k, v in originals.items():
            setattr(reg, k, v)
    assert "channel_updates" in [j["name"] for j in app.job_queue.daily]
