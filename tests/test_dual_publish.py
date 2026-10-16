"""Dual-surface publish — Telegram AND site, deduped, queued on site outage.

CONTENT directive (2026-10-16): content must land on BOTH surfaces. These
tests pin the contract: a successful mirror reports ``site ✅``; a site outage
QUEUES the item (``site ⏳ queued``) and the queue drains on the next publish;
the status line names both surfaces.
"""

from __future__ import annotations

import json

import pytest

from rugmunchbot.support import dual_publish as dp


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Point the feed store at a temp dir so tests never touch the real store."""
    monkeypatch.setattr(dp, "_feed_data_dir", lambda: str(tmp_path))
    return tmp_path


def test_publish_to_site_success_reports_site_ok(monkeypatch):
    monkeypatch.setattr(dp, "_mirror", lambda item: "post-123")
    st = dp.publish_to_site({"text": "hi", "story_ref": "ref-1"})
    assert st.site is True
    assert st.queued is False
    assert st.slug == "post-123"
    assert st.feed_url == "https://rugmunch.io/blog/post-123"
    assert "site ✅" in st.line()


def test_publish_to_site_failure_queues(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_mirror", lambda item: None)
    st = dp.publish_to_site({"text": "hi", "story_ref": "ref-2"})
    assert st.site is False
    assert st.queued is True
    assert "site ⏳ queued" in st.line()
    queue = json.loads((tmp_path / "feed_retry_queue.json").read_text())
    assert len(queue) == 1
    assert queue[0]["story_ref"] == "ref-2"


def test_queue_drains_when_site_recovers(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_mirror", lambda item: None)
    dp.publish_to_site({"text": "hi", "story_ref": "ref-3"})
    # Site recovers — the next drain flushes the backlog.
    monkeypatch.setattr(dp, "_mirror", lambda item: "post-999")
    landed = dp.drain_retry_queue()
    assert landed == 1
    assert json.loads((tmp_path / "feed_retry_queue.json").read_text()) == []


def test_queue_dedupes_by_story_ref(monkeypatch, tmp_path):
    monkeypatch.setattr(dp, "_mirror", lambda item: None)
    dp.publish_to_site({"text": "a", "story_ref": "ref-4"})
    dp.publish_to_site({"text": "b", "story_ref": "ref-4"})
    queue = json.loads((tmp_path / "feed_retry_queue.json").read_text())
    assert len(queue) == 1


def test_status_line_names_both_surfaces():
    st = dp.PublishStatus(telegram=True, site=True, slug="post-1")
    assert st.line() == "Telegram ✅ / site ✅ (post-1)"
    st2 = dp.PublishStatus(telegram=True, queued=True)
    assert st2.line() == "Telegram ✅ / site ⏳ queued"


def test_missing_story_ref_is_an_error():
    st = dp.publish_to_site({"text": "no ref"})
    assert st.site is False
    assert st.queued is False
    assert st.errors
