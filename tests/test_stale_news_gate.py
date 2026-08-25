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

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

# ── bot layer: _recency_gate / _draft_age_hours ──────────────────────


def _iso(hours_ago: float) -> str:
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


def test_recency_gate_rejects_undated():
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    assert _recency_gate({"text": "Some exploit happened"}) == "undated"
    assert _recency_gate({}) == "undated"


def test_recency_gate_rejects_stale_ts_field():
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    item = {"ts": _iso(200 * 24)}  # 200 days old
    reason = _recency_gate(item)
    assert reason.startswith("stale(")
    assert "via field" in reason


def test_recency_gate_accepts_fresh_ts_field():
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    assert _recency_gate({"ts": _iso(5)}) == ""


def test_recency_gate_probes_embedded_iso_date():
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    item = {"text": "Rivus DAO exploit on 2024-09-16 drained funds."}
    reason = _recency_gate(item)
    assert reason.startswith("stale(")
    assert "via text" in reason


def test_recency_gate_probes_embedded_long_form_date():
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    item = {"text": "Kalax suffered a rugpull on October 14, 2024 draining $350k."}
    assert _recency_gate(item).startswith("stale(")


def test_recency_gate_fresh_embedded_date_passes():
    from rugmunchbot.channels.scheduler._posts_rag import _recency_gate

    year = datetime.now(UTC).year
    item = {"text": f"Exploit detected on {year}-08-25 affecting the vault."}
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
async def test_post_loop_gates_stale_draft_before_send():
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


def test_story_age_hours_parses_and_measures():
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
