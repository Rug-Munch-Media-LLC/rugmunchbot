"""Main-channel human-in-the-loop approval gate (2026-09-04).

Covers the opt-in ``RMI_MAIN_APPROVAL_GATE``:

* Disabled (default) → posts directly, no approval request to @rmi_admin_bot.
* Enabled + admin chat set → sends the draft to the admin bot for approval and
  does NOT post to main in the same tick.
* Approve callback → posts the draft to main.
* Reject callback → discards (no post) and keeps the dedupe mark.
* No response (auto-post) → posts to main (fail-open).
* Enabled but admin chat unset → fails open and posts directly.

The actual photo/quality-gate send path is stubbed (``_post_photo_aware``) so
these tests exercise the gate ROUTING logic in isolation — the image/quality
gate has its own dedicated tests.

NOTE: the dedupe ``_remember`` persists to a live Redis in this environment,
so every test uses a UNIQUE story ref to stay deterministic across runs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from rugmunchbot.channels.scheduler import _core, _posts_rag
from rugmunchbot.channels.scheduler._posts_rag import _approval


@pytest.fixture(autouse=True)
def _fresh_dedupe(monkeypatch):
    monkeypatch.setattr(_core, "_posted_keys", {})
    return


@pytest.fixture(autouse=True)
def _fresh_pending(monkeypatch):
    monkeypatch.setattr(_approval, "_pending", {})
    return


@pytest.fixture(autouse=True)
def _isolate_feed_data(monkeypatch, tmp_path):
    monkeypatch.setattr(_posts_rag, "FEED_DATA", str(tmp_path))
    return


def _draft(ref: str | None = None) -> dict[str, Any]:
    return {
        "story_ref": ref or f"ref-{uuid.uuid4().hex[:12]}",
        "text": (
            "Whale wallet 0xabc moved 12,400 ETH to Binance hot wallets "
            "overnight, the largest single transfer this month. Exchange "
            "inflows of this size have preceded every major distribution "
            "window this quarter. Source: https://example.com/x "
            "https://example.com/y https://example.com/z"
        ),
        "kind": "story",
        "mirror": False,
        "ts": datetime.now(UTC).isoformat(),
    }


class _Bot:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []  # (chat_id, text) — admin requests

    async def send_message(self, chat_id, text=None, **k):
        self.sent.append((chat_id, str(text or "")))
        return _Msg(chat_id)

    async def edit_message_text(self, chat_id=None, message_id=None, text=None, **k):
        return True


class _Msg:
    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.message_id = 42


class _Ctx:
    def __init__(self, bot: _Bot | None = None) -> None:
        self.bot = bot or _Bot()
        self.job_queue = None


class _Job:
    def __init__(self, data: str) -> None:
        self.data = data


def _patch_env(monkeypatch, *, chat_id: str, drafts: list[dict[str, Any]]):
    async def _fetch(channel: str, limit: int = 3) -> list[dict[str, Any]]:
        return [dict(d) for d in drafts][:limit]

    async def _no_news(limit=5):
        return []

    async def _resolve_image(channel, image_url="", source_url="", logger=None):
        return ""

    from rugmunchbot.support.quality_gate import GateResult

    async def _gate(text, image_url="", source=None, kind=None, **kw):
        return GateResult(ok=True, text=text, image_url=image_url or "")

    monkeypatch.setattr(_posts_rag, "fetch_rag_drafts", _fetch)
    monkeypatch.setattr(_posts_rag, "_chat_id", lambda ch: chat_id)
    monkeypatch.setattr("rugmunchbot.channels.content.fetch_trending_news", _no_news)
    monkeypatch.setattr("rugmunchbot.support.image_policy.resolve_post_image", _resolve_image)
    monkeypatch.setattr("rugmunchbot.support.quality_gate.pre_post_quality_gate", _gate)


def _stub_post(monkeypatch) -> list[tuple[str, str]]:
    """Stub the photo-aware send; returns a recorder of (chat_id, text)."""
    calls: list[tuple[str, str]] = []

    async def _post(bot, chat_id, post, safe_send, *, content_type=None):
        calls.append((chat_id, str(post)))
        return True

    monkeypatch.setattr(_posts_rag, "_post_photo_aware", _post)
    return calls


@pytest.mark.asyncio
async def test_gate_disabled_posts_directly(monkeypatch):
    """Default (gate off) → current behavior: post straight to main, no
    approval request to the admin bot."""
    monkeypatch.setattr(_posts_rag, "RMI_MAIN_APPROVAL_GATE", False)
    bot = _Bot()
    posts = _stub_post(monkeypatch)
    _patch_env(monkeypatch, chat_id="-100main", drafts=[_draft()])

    posted = await _posts_rag.post_rag_drafts(_Ctx(bot))

    assert posted == 1
    assert len(posts) == 1
    assert posts[0][0] == "-100main"
    # No approval request was sent to the admin bot.
    assert bot.sent == []


@pytest.mark.asyncio
async def test_gate_enabled_sends_approval_not_post(monkeypatch):
    """Gate on + admin chat set → the draft goes to @rmi_admin_bot for
    approval and is NOT posted to main in the same tick."""
    monkeypatch.setattr(_posts_rag, "RMI_MAIN_APPROVAL_GATE", True)
    monkeypatch.setattr(_posts_rag, "RMI_ADMIN_CHAT_ID", "-100admin")
    bot = _Bot()
    posts = _stub_post(monkeypatch)
    _patch_env(monkeypatch, chat_id="-100main", drafts=[_draft()])

    posted = await _posts_rag.post_rag_drafts(_Ctx(bot))

    # Nothing posted to main yet (awaiting approval).
    assert posted == 0
    assert posts == []
    # One approval request went to the admin bot.
    assert len(bot.sent) == 1
    assert bot.sent[0][0] == "-100admin"
    assert "APPROVAL REQUIRED" in bot.sent[0][1]
    # A pending approval was recorded.
    assert len(_approval._pending) == 1


@pytest.mark.asyncio
async def test_approve_callback_posts_to_main(monkeypatch):
    """Approve → the draft is posted to the main channel."""
    monkeypatch.setattr(_posts_rag, "RMI_MAIN_APPROVAL_GATE", True)
    monkeypatch.setattr(_posts_rag, "RMI_ADMIN_CHAT_ID", "-100admin")
    bot = _Bot()
    posts = _stub_post(monkeypatch)
    _patch_env(monkeypatch, chat_id="-100main", drafts=[_draft()])

    await _posts_rag.post_rag_drafts(_Ctx(bot))
    approval_id = next(iter(_approval._pending))

    # Simulate the operator pressing Approve.
    await _approval._approve_main_approval(_FakeUpdate(), _Ctx(bot), approval_id)

    assert len(posts) == 1
    assert posts[0][0] == "-100main"
    assert len(_approval._pending) == 0


@pytest.mark.asyncio
async def test_reject_callback_discards(monkeypatch):
    """Reject → no post to main, dedupe mark kept (discarded, not retried)."""
    monkeypatch.setattr(_posts_rag, "RMI_MAIN_APPROVAL_GATE", True)
    monkeypatch.setattr(_posts_rag, "RMI_ADMIN_CHAT_ID", "-100admin")
    bot = _Bot()
    posts = _stub_post(monkeypatch)
    _patch_env(monkeypatch, chat_id="-100main", drafts=[_draft()])

    await _posts_rag.post_rag_drafts(_Ctx(bot))
    approval_id = next(iter(_approval._pending))

    await _approval._reject_main_approval(_FakeUpdate(), _Ctx(bot), approval_id)

    assert posts == []
    assert len(_approval._pending) == 0
    # Dedupe mark kept → a second tick posts nothing.
    await _posts_rag.post_rag_drafts(_Ctx(bot))
    assert posts == []


@pytest.mark.asyncio
async def test_auto_post_fires_after_timeout(monkeypatch):
    """No response → the auto-post callback posts to main (fail-open)."""
    monkeypatch.setattr(_posts_rag, "RMI_MAIN_APPROVAL_GATE", True)
    monkeypatch.setattr(_posts_rag, "RMI_ADMIN_CHAT_ID", "-100admin")
    bot = _Bot()
    posts = _stub_post(monkeypatch)
    _patch_env(monkeypatch, chat_id="-100main", drafts=[_draft()])

    await _posts_rag.post_rag_drafts(_Ctx(bot))
    approval_id = next(iter(_approval._pending))

    # Simulate the scheduled auto-post firing.
    ctx = _Ctx(bot)
    ctx.job = _Job(approval_id)
    await _approval._auto_post_main_approval(ctx)

    assert len(posts) == 1
    assert posts[0][0] == "-100main"
    assert len(_approval._pending) == 0


@pytest.mark.asyncio
async def test_gate_enabled_no_admin_chat_fails_open(monkeypatch):
    """Gate on but admin chat unset → fail open and post directly (never
    drop content)."""
    monkeypatch.setattr(_posts_rag, "RMI_MAIN_APPROVAL_GATE", True)
    monkeypatch.setattr(_posts_rag, "RMI_ADMIN_CHAT_ID", "")
    bot = _Bot()
    posts = _stub_post(monkeypatch)
    _patch_env(monkeypatch, chat_id="-100main", drafts=[_draft()])

    posted = await _posts_rag.post_rag_drafts(_Ctx(bot))

    assert posted == 1
    assert len(posts) == 1
    assert posts[0][0] == "-100main"
    assert len(_approval._pending) == 0


class _FakeUpdate:
    def __init__(self) -> None:
        self.callback_query = None
