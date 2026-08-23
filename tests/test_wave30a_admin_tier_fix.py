"""Test wave 30a — /admin_set_tier days-parsing hardening (TOP-100 #34).

Regression: int(ctx.args[2]) sat OUTSIDE the guarded parse block — a
non-numeric days arg crashed the handler uncaught, and values were unbounded.
Behavior probed against the fixed handler (2026-08-23).
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot.config import TIERS
from rugmunchbot.handlers.owner import _users as u


class FakeMessage:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kw: Any) -> None:
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, uid: int = 1) -> None:
        self.message = FakeMessage()
        self.effective_user = type("U", (), {"id": uid})()


class FakeCtx:
    def __init__(self, args: list[str]) -> None:
        self.args = args


@pytest.fixture
def owner_env(monkeypatch):
    monkeypatch.setattr(u, "is_owner", lambda uid: True)
    tier_calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(u.db, "get_user_by_id", lambda tid: {"id": tid})
    monkeypatch.setattr(
        u.db, "set_user_tier", lambda tid, tier, days: tier_calls.append((tid, tier, days))
    )
    return tier_calls


@pytest.mark.asyncio
async def test_non_owner_denied(monkeypatch):
    monkeypatch.setattr(u, "is_owner", lambda uid: False)
    calls: list[str] = []

    async def _deny(update, ctx) -> None:
        calls.append("denied")

    monkeypatch.setattr(u, "_deny_admin_attempt", _deny)
    upd = FakeUpdate()
    await u.cmd_admin_set_tier(upd, FakeCtx(["123", next(iter(TIERS))]))
    assert calls == ["denied"]
    assert upd.message.replies == []


@pytest.mark.asyncio
async def test_non_numeric_days_rejected_not_crash(owner_env):
    # Pre-fix this raised an uncaught ValueError out of the handler.
    upd = FakeUpdate()
    await u.cmd_admin_set_tier(upd, FakeCtx(["123", next(iter(TIERS)), "abc"]))
    assert any("Invalid days value" in r for r in upd.message.replies)
    assert owner_env == []


@pytest.mark.asyncio
async def test_days_below_one_rejected(owner_env):
    upd = FakeUpdate()
    await u.cmd_admin_set_tier(upd, FakeCtx(["123", next(iter(TIERS)), "0"]))
    assert any("between 1 and 3650" in r for r in upd.message.replies)
    assert owner_env == []


@pytest.mark.asyncio
async def test_unbounded_days_rejected(owner_env):
    upd = FakeUpdate()
    await u.cmd_admin_set_tier(upd, FakeCtx(["123", next(iter(TIERS)), "99999"]))
    assert any("between 1 and 3650" in r for r in upd.message.replies)
    assert owner_env == []


@pytest.mark.asyncio
async def test_valid_explicit_days_applied(owner_env):
    tier = next(iter(TIERS))
    upd = FakeUpdate()
    await u.cmd_admin_set_tier(upd, FakeCtx(["123", tier, "90"]))
    assert owner_env == [(123, tier, 90)]
    assert any("\u2705 Set user 123" in r and "(90 days)" in r for r in upd.message.replies)


@pytest.mark.asyncio
async def test_default_days_30_when_omitted(owner_env):
    tier = next(iter(TIERS))
    upd = FakeUpdate()
    await u.cmd_admin_set_tier(upd, FakeCtx(["123", tier]))
    assert owner_env == [(123, tier, 30)]


@pytest.mark.asyncio
async def test_unknown_user_still_blocked_before_set(owner_env, monkeypatch):
    monkeypatch.setattr(u.db, "get_user_by_id", lambda tid: None)
    upd = FakeUpdate()
    await u.cmd_admin_set_tier(upd, FakeCtx(["404", next(iter(TIERS))]))
    assert any("not found" in r for r in upd.message.replies)
    assert owner_env == []
