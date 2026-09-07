"""Stale-news incident fix (2026-08-26) — recency gates on both layers.

Incident: @cryptorugmuncher posted a 2022-era exploit. Root cause chain:
rugbrain's story selection was an importance-ranked vector search over ALL
history (incl. the undated threat_intel_v2 hack KB) and emitted drafts with
NO timestamp; the bot had no recency gate either.

* rugbrain layer — only news_v2 (dated), age-filtered, ts carried through.
* bot layer — fail-closed recency gate: undated or >72h drafts never post,
  embedded dates in text are probed as a fallback.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

# Deterministic clock: every time-dependent assertion is pinned to a fixed
# "now" so the suite never drifts with the wall clock (the old hardcoded
# dates went stale as the calendar moved on). FROZEN_NOW is a stable anchor
# far enough in the past that "200 days old" and "2 hours old" are both
# unambiguous, and far enough from any real-world date that embedded-date
# probes (2024) are always stale.
#
# We patch the `datetime` class directly (unittest.mock) rather than using
# freezegun: freezegun's stop() iterates dir() over every loaded module, and
# transformers' lazy __dir__ triggers a broken llava_next_video import in this
# venv. Patching the specific modules' datetime reference is deterministic and
# avoids that whole class of failure.
FROZEN_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


class _FrozenDateTime(datetime):
    """datetime stand-in whose now() always returns FROZEN_NOW."""

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_NOW
        return FROZEN_NOW.astimezone(tz)


@pytest.fixture
def frozen_now():
    """Pin datetime.now() to FROZEN_NOW in every module under test."""
    targets = ["rugmunchbot.channels.scheduler._posts_rag._gates.datetime"]
    # rugbrain is a sibling repo, not always installed in this venv — only
    # patch it when present so the bot-layer tests stay runnable standalone.
    try:
        import rugbrain.content_engine  # noqa: F401
    except ImportError:
        pass
    else:
        targets.append("rugbrain.content_engine.datetime")
    with patch(targets[0], _FrozenDateTime), patch(targets[1], _FrozenDateTime):
        yield FROZEN_NOW


# ── bot layer: _recency_gate / _draft_age_hours ──────────────────────


def _iso(hours_ago: float) -> str:
    return (FROZEN_NOW - timedelta(hours=hours_ago)).isoformat()


def test_recency_gate_rejects_undated():
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    assert _recency_gate({"text": "Some exploit happened"}) == "undated"
    assert _recency_gate({}) == "undated"


def test_recency_gate_rejects_stale_ts_field(frozen_now):
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    item = {"ts": _iso(200 * 24)}  # 200 days old
    reason = _recency_gate(item)
    assert reason.startswith("stale(")
    assert "via field" in reason


def test_recency_gate_accepts_fresh_ts_field(frozen_now):
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    assert _recency_gate({"ts": _iso(5)}) == ""


def test_recency_gate_probes_embedded_iso_date(frozen_now):
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    item = {"text": "Rivus DAO exploit on 2024-09-16 drained funds."}
    reason = _recency_gate(item)
    assert reason.startswith("stale(")
    assert "via text" in reason


def test_recency_gate_probes_embedded_long_form_date(frozen_now):
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    item = {"text": "Kalax suffered a rugpull on October 14, 2024 draining $350k."}
    assert _recency_gate(item).startswith("stale(")


def test_recency_gate_fresh_embedded_date_passes(frozen_now):
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    # Relative to the frozen clock — deterministic regardless of wall time.
    fresh = (FROZEN_NOW - timedelta(hours=2)).strftime("%Y-%m-%d")
    item = {"text": f"Exploit detected on {fresh} affecting the vault."}
    assert _recency_gate(item) == ""


def test_recency_gate_disabled_via_env(monkeypatch):
    import importlib

    import rugmunchbot.channels.scheduler._posts_rag as pr

    monkeypatch.setenv("RMI_RAG_POST_MAX_AGE_H", "0")
    importlib.reload(pr)
    try:
        assert pr._recency_gate({}) == ""  # gate off → undated passes
    finally:
        monkeypatch.delenv("RMI_RAG_POST_MAX_AGE_H")
        importlib.reload(pr)


@pytest.mark.asyncio
async def test_post_loop_gates_stale_draft_before_send(frozen_now):
    """End-to-end: a stale draft is dropped and NEVER sent to Telegram."""
    from rugmunchbot.channels.scheduler import _posts_rag

    stale = {
        "text": "Fortress Loans suffered an exploit (2023).",
        "story_ref": "defihacklabs",
        "mirror": True,
        "kind": "story",
        "writer": "llm",
        "score": 0.9,
    }
    sent: list[str] = []

    class _Bot:
        async def send_message(self, chat_id, text, **_kw):
            sent.append(text)

    async def _fake_fetch(channel, limit=0):
        return [stale] if channel == "main" else []

    with (
        patch.object(_posts_rag, "fetch_rag_drafts", _fake_fetch),
        patch.object(_posts_rag, "_chat_id", return_value="-100main"),
        patch.object(_posts_rag, "_remember", return_value=True),
    ):
        posted = await _posts_rag.post_rag_drafts(SimpleNamespaceCtx(_Bot()))
    assert posted == 0
    assert sent == [], "stale draft must never reach Telegram"


class SimpleNamespaceCtx:
    """Minimal ContextTypes stand-in carrying only `.bot`."""

    def __init__(self, bot):
        self.bot = bot


# ── rugbrain layer: selection freshness ──────────────────────────────
# These tests inspect the SIBLING repo's content engine. rugbrain is an
# independent repo and is NOT installed in this bot's venv — skip cleanly
# when absent (run them from the rugbrain checkout / CI job instead).

pytest.importorskip("rugbrain", reason="sibling repo not installed in bot venv")


def test_story_age_hours_parses_and_measures(frozen_now):
    from rugbrain.content_engine import _story_age_hours

    fresh = {"ts": _iso(3)}
    old = {"ts": "2024-09-16T00:00:00+00:00"}
    undated = {}
    bad = {"ts": "not-a-date"}
    assert _story_age_hours(fresh) is not None
    assert _story_age_hours(fresh) < 24
    assert _story_age_hours(old) > 10000
    assert _story_age_hours(undated) is None
    assert _story_age_hours(bad) is None


def test_recent_stories_drops_threat_intel_collection():
    """threat_intel_v2 (undated historical KB) must not be probed at all."""
    import inspect

    from rugbrain import content_engine

    src = inspect.getsource(content_engine._recent_stories)
    # the selection LOOP must reference only news_v2 (comments may explain
    # WHY threat_intel_v2 was dropped — that's fine, code is not)
    loop_lines = [ln for ln in src.split("\n") if "for collection in" in ln]
    assert len(loop_lines) == 1
    assert '"news_v2"' in loop_lines[0]
    assert "threat_intel_v2" not in loop_lines[0]


def test_draft_payload_carries_ts():
    """generate_channel_drafts must include the story ts in each draft."""
    import inspect

    from rugbrain import content_engine

    src = inspect.getsource(content_engine.generate_channel_drafts)
    assert '"ts"' in src
    assert 'story.get("ts")' in src
