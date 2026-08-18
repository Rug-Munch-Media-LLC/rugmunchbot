"""Rate limiting + flood control (hermetic — no live Redis dependency)."""

import pytest

from rugmunchbot._vendor.rag_pkg.rate_limiter import RateLimiter
from rugmunchbot.flood_control import FloodController


class _FakeRedis:
    """Minimal in-memory Redis stand-in for RateLimiter's zset ops."""

    def __init__(self) -> None:
        self._z: dict[str, dict[str, float]] = {}

    def zremrangebyscore(self, key: str, lo: int, hi: int) -> None:
        if key in self._z:
            self._z[key] = {m: s for m, s in self._z[key].items() if not (lo <= s <= hi)}

    def zcard(self, key: str) -> int:
        return len(self._z.get(key, {}))

    def zadd(self, key: str, mapping: dict[str, float]) -> None:
        self._z.setdefault(key, {}).update(mapping)

    def expire(self, key: str, ttl: int) -> None:
        pass

    def pipeline(self) -> "_FakeRedis":
        return self

    def execute(self) -> list[None]:
        return []


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point BOT_DB_PATH at a temp sqlite and disable redis."""
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot_users.db"))
    from rugmunchbot import db

    db.init_db()
    return db


@pytest.mark.asyncio
async def test_rate_limiter_blocks_over_budget(monkeypatch):
    """With Redis up, the 6th request in the window must be blocked."""
    fake = _FakeRedis()
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: fake)

    limiter = RateLimiter(max_per_minute=5)
    for i in range(5):
        ok = await limiter.check()
        assert ok is True, f"request {i} should pass"
    ok = await limiter.check()
    assert ok is False, "6th request must be blocked"


@pytest.mark.asyncio
async def test_rate_limiter_fail_open_without_redis(monkeypatch):
    """No Redis = fail-open (return True), never block the pipeline."""
    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", lambda: None)

    limiter = RateLimiter(max_per_minute=1)
    for _ in range(3):
        ok = await limiter.check()
        assert ok is True, "fail-open: must never block when Redis is down"


@pytest.mark.asyncio
async def test_rate_limiter_fail_open_on_redis_error(monkeypatch):
    """Redis raising = fail-open too (log + allow)."""

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr("rugmunchbot._vendor.redis_client.get_redis", _boom)

    limiter = RateLimiter(max_per_minute=5)
    assert await limiter.check() is True


@pytest.mark.asyncio
async def test_flood_control_marks_abuse(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot_users.db"))
    from rugmunchbot.flood_control import FloodController

    fc = FloodController(per_second=3, per_minute=20, redis_enabled=False)
    user = 777001
    for _ in range(3):
        ok, _, _ = await fc.check_and_count(user)
        assert ok is True, "request should pass"
    ok, _, _ = await fc.check_and_count(user)
    assert ok is False


@pytest.mark.asyncio
async def test_flood_refund(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot_users.db"))
    fc = FloodController(per_second=2, per_minute=20, redis_enabled=False)
    ok, used, receipt = await fc.check_and_count(555001)
    assert ok is True
    assert receipt
    await fc.refund(555001, receipt)
    _, used2, _ = await fc.check_and_count(555001)
    assert used2 <= used
