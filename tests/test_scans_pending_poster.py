"""Scans-of-interest poster tests — "Scanning…" pending teasers vs full cards.

Contract (2026-08-29 scans-of-interest fix):
* A ``scan_pending`` note (deep phases still running) posts as a "Scanning…"
  teaser under its OWN dedupe namespace so the FULL card for the same token
  can still post later — never "Deep analysis unavailable".
* Pending teasers never escalate to the main channel.
* Both classes are bounded by the per-tick cap.
"""

from __future__ import annotations

import pytest


def test_pending_namespace_does_not_collide_with_full_card() -> None:
    """A pending teaser's dedupe key (``pending_<addr>``) must not collide
    with the full card's key (``<addr>``) in the global seen-set — posting
    "Scanning…" must never black-hole the final card."""
    from rugmunchbot.channels.scheduler import _core

    _core._posted_keys.clear()
    try:
        # Teaser posts first…
        assert _core._remember("scans_pending", "pending_0xabc") is True
        # …and the full card for the SAME token is still postable later.
        full_key = _core._dedupe_key("scans", {"address": "0xabc", "title": "T"})
        assert full_key == "scans:0xabc"
        assert _core._remember("scans", full_key) is True
        # Same token teaser is deduped (no hourly "Scanning…" spam).
        assert _core._remember("scans_pending", "pending_0xabc") is False
        # A different token is unaffected.
        assert _core._remember("scans", _core._dedupe_key("scans", {"address": "0xdef"})) is True
    finally:
        _core._posted_keys.clear()


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def __call__(
        self, bot: object, chat_id: str, post: object, _send: object, **kw: object
    ) -> bool:
        text = str(post)
        addr = "pending" if "Scanning\u2026" in text else "full"
        self.calls.append(addr)
        return True


class _Bot:
    pass


class _Ctx:
    bot = _Bot()


async def _patch_env(monkeypatch: pytest.MonkeyPatch, *, notes: list[dict]) -> _Recorder:
    """Wire the scans poster to a fixed note pool and record sends."""
    import rugmunchbot.channels.scheduler._posts_scan as ps

    async def _fetch_scan_notes(limit: int = 10) -> list[dict]:
        return []

    async def _fetch_suspect(limit: int = 4) -> list[dict]:
        return [dict(n) for n in notes][:limit]

    async def _no_commentary(item: dict) -> dict:
        return item

    async def _score_and_partition(fresh, category=None, source=None):
        # Every full item stays in the keep lane; nothing escalates to main.
        return [], list(fresh), False

    recorder = _Recorder()
    monkeypatch.setattr(ps, "fetch_scan_notes", _fetch_scan_notes)
    monkeypatch.setattr(ps, "fetch_suspect_scan_notes", _fetch_suspect)
    monkeypatch.setattr(ps, "attach_scan_commentary", _no_commentary)
    monkeypatch.setattr(ps, "_score_and_partition", _score_and_partition)
    monkeypatch.setattr(ps, "_chat_id", lambda ch: "-100scans" if ch == "scans" else None)
    monkeypatch.setattr(ps, "_post_photo_aware", recorder)
    return recorder


@pytest.mark.asyncio
async def test_pending_teaser_posts_and_full_card_represents(monkeypatch) -> None:
    """Both a pending teaser and a full card post; the pending one carries
    its own namespace so it is not re-teased next tick while a fresh full
    card still posts."""
    from rugmunchbot.channels.scheduler import _core

    _core._posted_keys.clear()
    try:
        recorder = await _patch_env(
            monkeypatch,
            notes=[
                {"address": "0xaaa", "scan_pending": True, "churned": True},
                {"address": "0xbbb", "risk_flags": ["x"], "churned": True},
            ],
        )
        import rugmunchbot.channels.scheduler._posts_scan as ps

        posted = await ps.post_scan_update(_Ctx())
        assert posted == 2
        assert sorted(recorder.calls) == ["full", "pending"]
        # Teaser was remembered under its own namespace…
        assert "pending_0xaaa" in _core._posted_keys.get("__global__", set())
        # …while the full card key was recorded normally (no collision).
        assert "0xbbb" in _core._posted_keys.get("__global__", set())

        # Next tick: same pending token is NOT re-teased; a new full token posts.
        recorder2 = await _patch_env(
            monkeypatch,
            notes=[
                {"address": "0xaaa", "scan_pending": True, "churned": True},
                {"address": "0xccc", "risk_flags": ["x"], "churned": True},
            ],
        )
        posted2 = await ps.post_scan_update(_Ctx())
        assert posted2 == 1
        assert recorder2.calls == ["full"]
    finally:
        _core._posted_keys.clear()
