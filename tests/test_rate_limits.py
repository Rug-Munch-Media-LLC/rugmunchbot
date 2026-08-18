"""Rate limiting + flood control (SQLite fallback paths, no redis)."""

import pytest

from rugmunchbot._vendor.rag_pkg.rate_limiter import RateLimiter
from rugmunchbot.flood_control import FloodController


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Point BOT_DB_PATH at a temp sqlite and disable redis."""
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "bot_users.db"))
    from rugmunchbot import db

    db.init_db()
    return db
    # db.close() - no close method, just let it go out of scope


@pytest.mark.asyncio
async def test_rate_limiter_sqlite_fallback(isolated_db, monkeypatch):

    limiter = RateLimiter(max_per_minute=5)
    for i in range(5):
        ok = await limiter.check(424242)
        assert ok is True, f"request {i} should pass"
    ok = await limiter.check(424242)
    assert ok is False


@pytest.mark.asyncio
async def test_flood_control_marks_abuse(tmp_path, monkeypatch):
    from rugmunchbot.flood_control import FloodController

    fc = FloodController(per_second=3, per_minute=20, redis_enabled=False)
    user = 777001
    for _ in range(3):
        ok, _, _ = await fc.check_and_count(user)
        assert ok is True, "request should pass"
    ok, _, _ = await fc.check_and_count(user)
    assert ok is False
    # msg is None when blocked (no retry_after in sqlite fallback)
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
