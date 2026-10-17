"""No-network tests for the briefing publication ledger (telegram_news._ledger).

Oracle note (AI-TEST-ORACLE-POLICY): each test drives the real ledger
helpers against a hand-built fake Redis and asserts exact outcomes — blank
reports are blocked, short reports are blocked, near-duplicates of the
last N are blocked, fresh reports publish, and image selection never
returns a banned X/Telegram profile image while always falling back to
the RugMunch Intelligence logo.
"""

from __future__ import annotations

import logging

import pytest

from rugmunchbot.channels.telegram_news import _ledger as ledger


class _FakeRedis:
    """Minimal sync Redis stand-in: LPOP/LRANGE/LTRIM/EXPIRE + SADD."""

    def __init__(self, *, down: bool = False) -> None:
        self.lists: dict[str, list[str]] = {}
        self.ttls: dict[str, int] = {}
        self.down = down

    def lpush(self, key: str, value: str) -> int:
        if self.down:
            raise ConnectionError("redis down")
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def lrange(self, key: str, start: int, end: int) -> list[bytes]:
        if self.down:
            raise ConnectionError("redis down")
        vals = self.lists.get(key, [])
        # Mimic redis-py returning bytes.
        return [v.encode() for v in vals[start : end + 1]]

    def ltrim(self, key: str, start: int, end: int) -> bool:
        if self.down:
            raise ConnectionError("redis down")
        if key in self.lists:
            self.lists[key] = self.lists[key][start : end + 1]
        return True

    def expire(self, key: str, ttl: int) -> bool:  # noqa: ARG002
        if self.down:
            raise ConnectionError("redis down")
        self.ttls[key] = ttl
        return True


def _patch_redis(monkeypatch: pytest.MonkeyPatch, fake: _FakeRedis) -> None:
    # Patch the seam the ledger actually calls. ``_ledger._get_client`` is the
    # single indirection point, and the autouse ``_isolate_publication_ledger``
    # fixture in conftest.py stubs it to ``None`` for every test — so patching
    # ``redis_client.get_redis`` alone is shadowed and the fake never reaches
    # the ledger. Override ``_get_client`` directly (monkeypatch restores it).
    monkeypatch.setattr(ledger, "_get_client", lambda: fake if not fake.down else None)


def test_content_hash_is_stable_and_normalized() -> None:
    text = "Hello   world\n\n  foo bar"
    assert ledger.content_hash(text) == ledger.content_hash(text)
    assert ledger.content_hash(text) == ledger.content_hash("Hello world foo bar")
    assert ledger.content_hash(text) != ledger.content_hash("Hello world foo baz")


def test_empty_report_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    result = ledger.evaluate_report("", namespace="morning_rug", min_words=5)
    assert result.allowed is False
    assert result.reason == "content_blank_or_dup:empty_report"
    assert result.content_hash == ledger.content_hash("")


def test_short_report_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    result = ledger.evaluate_report("one two three", namespace="midday_update", min_words=100)
    assert result.allowed is False
    assert result.reason == "content_blank_or_dup:too_short"
    assert "too_short" in result.reason


def test_fresh_report_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    report = " ".join(["word"] * 120)
    result = ledger.evaluate_report(report, namespace="morning_rug", min_words=100)
    assert result.allowed is True
    assert result.reason == "ok"


def test_duplicate_recent_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    report = " ".join(["word"] * 120)
    h = ledger.content_hash(report)
    ledger.record_publication("morning_rug", h)
    # Re-running the SAME report must be blocked.
    result = ledger.evaluate_report(report, namespace="morning_rug", min_words=100)
    assert result.allowed is False
    assert result.reason == "content_blank_or_dup:recent_duplicate"


def test_duplicate_outside_history_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A duplicate older than the last N is no longer a recent dup."""
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    report = " ".join(["word"] * 120)
    h = ledger.content_hash(report)
    # Fill the ledger to n_history with OTHER hashes, evicting the dup.
    for i in range(ledger.DEFAULT_HISTORY):
        ledger.record_publication("morning_rug", f"other-{i}")
    ledger.record_publication("morning_rug", h)  # now most recent
    # Push 10 newer ones so h falls out of the lrange window.
    for i in range(ledger.DEFAULT_HISTORY):
        ledger.record_publication("morning_rug", f"newer-{i}")
    result = ledger.evaluate_report(report, namespace="morning_rug", min_words=100)
    assert result.allowed is True


def test_fail_open_on_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage must never block a legitimately clean report."""
    fake = _FakeRedis(down=True)
    _patch_redis(monkeypatch, fake)
    report = " ".join(["word"] * 120)
    result = ledger.evaluate_report(report, namespace="morning_rug", min_words=100)
    assert result.allowed is True
    # record_publication must not raise either.
    ledger.record_publication("morning_rug", ledger.content_hash(report))


def test_fail_open_no_redis_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ledger, "_get_client", lambda: None)
    report = " ".join(["word"] * 120)
    # Fresh text → not a known duplicate even without Redis.
    result = ledger.evaluate_report(report, namespace="morning_rug", min_words=100)
    assert result.allowed is True


def test_select_edition_image_keeps_valid_candidate() -> None:
    img = "https://cdn.example.com/story.png"
    assert ledger.select_edition_image(candidate=img) == img


def test_select_edition_image_falls_back_to_rmi_logo() -> None:
    # Empty candidate -> the RugMunch Intelligence logo (app favicon).
    assert ledger.select_edition_image(candidate="") == ledger.RMI_LOGO_URL
    assert "rugmunch.io" in ledger.RMI_LOGO_URL
    assert "pbs.twimg.com" not in ledger.RMI_LOGO_URL


def test_select_edition_image_rejects_banned_profile_pic() -> None:
    """The cryptorugmunch X avatar URL is NEVER returned as a post image."""
    banned = "https://pbs.twimg.com/profile_images/123/abc.jpg"
    out = ledger.select_edition_image(candidate=banned)
    assert out == ledger.RMI_LOGO_URL
    assert "pbs.twimg.com" not in out


def test_blocked_report_emits_structured_log(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    caplog = _CapLog()
    result = ledger.evaluate_report("", namespace="morning_rug", min_words=5, log=caplog)
    assert result.allowed is False
    assert any("content_blank_or_dup:empty_report" in line for line in caplog.lines)


class _CapLog:
    """Minimal log stub mirroring the logger= interface used by the briefings."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def warning(self, fmt: str, *args: object) -> None:
        self.lines.append(fmt % args)
