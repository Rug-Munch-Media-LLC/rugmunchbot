"""Cross-edition dedup helper tests (2026-09-05).

Uses a fake Redis client — no live Redis dependency. Verifies the morning
mark/midday-exclude contract and fail-open behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rugmunchbot.channels import _edition_dedup as dedup

if TYPE_CHECKING:
    import pytest


class _FakeRedis:
    def __init__(self, *, down: bool = False) -> None:
        self.sets: dict[str, set[bytes]] = {}
        self.ttls: dict[str, int] = {}
        self.down = down

    def exists(self, key: str) -> int:
        return int(bool(self.sets.get(key)))

    def smembers(self, key: str) -> set[bytes]:
        return set(self.sets.get(key, set()))

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)

    def _exec(self, key: str, members: set[str], ttl: int) -> None:
        if self.down:
            raise ConnectionError("redis down")
        self.sets[key] = {m.encode() for m in members}
        self.ttls[key] = ttl


class _FakePipeline:
    def __init__(self, client: _FakeRedis) -> None:
        self._client = client
        self._ops: list[tuple[str, str, set[str] | int]] = []

    def __enter__(self) -> _FakePipeline:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def sadd(self, key: str, *members: str) -> _FakePipeline:
        self._ops.append(("sadd", key, set(members)))
        return self

    def expire(self, key: str, ttl: int) -> _FakePipeline:
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self) -> list[bool]:
        key = next(op[1] for op in self._ops if op[0] == "sadd")
        members = next(op[2] for op in self._ops if op[0] == "sadd")
        ttl = next(op[2] for op in self._ops if op[0] == "expire")
        self._client._exec(key, members, ttl)
        return [True, True]


def _story(url: str = "https://example.com/a", title: str = "T") -> dict[str, Any]:
    return {"url": url, "title": title}


def test_story_hash_prefers_url(monkeypatch: pytest.MonkeyPatch) -> None:
    assert dedup.story_hash(_story()) == dedup.story_hash(_story(title="ignored"))
    assert dedup.story_hash({"title": "t"}) != dedup.story_hash({"title": "other"})


def test_mark_then_exclude_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRedis()
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: fake)
    stories = [_story("https://x.com/1"), _story("https://x.com/2")]
    dedup.mark_morning_stories(stories)
    key = dedup.morning_posted_key()
    assert fake.exists(key)
    assert fake.ttls[key] == dedup.MORNING_POSTED_TTL
    kept = dedup.exclude_morning_stories([*_story_list(), *stories])
    assert kept == _story_list()


def _story_list() -> list[dict[str, Any]]:
    return [_story("https://x.com/3")]


def test_exclude_no_key_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: _FakeRedis())
    stories = [_story("https://x.com/9")]
    assert dedup.exclude_morning_stories(stories) == stories


def test_fail_open_on_redis_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: _FakeRedis(down=True))
    stories = [_story("https://x.com/1")]
    dedup.mark_morning_stories(stories)  # must not raise
    assert dedup.exclude_morning_stories(stories) == stories  # passthrough


def test_fail_open_on_no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: None)
    stories = [_story("https://x.com/1")]
    dedup.mark_morning_stories(stories)  # must not raise
    assert dedup.exclude_morning_stories(stories) == stories
