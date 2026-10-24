"""Raid / flood containment: burst detection, expiry, containment, release."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from rugmunchbot.moderation import audit, decide, guard_settings, quarantine, raidmode, state, trust
from rugmunchbot.moderation.guard_settings import GuardSettings
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    FakeBot,
    FakeChat,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    ctx,
    policy,
)

SETTINGS = GuardSettings(
    raid_joins=3, raid_window_s=30, raid_hold_s=60, flood_msgs=3, flood_window_s=10
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    audit.use_audit_path(tmp_path / "audit.jsonl")
    state.reset()
    raidmode.reset()
    quarantine.reset()
    trust.reset()
    for key in [k for k in os.environ if k.startswith("RUGMUNCH_MOD_")]:
        monkeypatch.delenv(key, raising=False)
    return


def test_join_burst_engages_raid_with_reason():
    signals = [raidmode.note_join(CHAT_ID, SETTINGS, now=1000.0 + i) for i in range(3)]
    assert [s.triggered for s in signals] == [False, False, True]
    assert "3 joins within 30s" in signals[-1].reason
    assert raidmode.is_active(CHAT_ID, now=1001.0) is True
    rows = audit.tail(CHAT_ID, 3)
    assert any(r["rule"] == "raid_detected" and r["action"] == "lockdown" for r in rows)


def test_raid_hold_expires_on_its_own():
    for i in range(3):
        raidmode.note_join(CHAT_ID, SETTINGS, now=1000.0 + i)
    assert raidmode.is_active(CHAT_ID, now=1030.0) is True
    assert raidmode.is_active(CHAT_ID, now=1100.0) is False
    assert raidmode.status(CHAT_ID, now=1100.0) is None


def test_message_flood_engages_the_same_reversible_state():
    for i in range(3):
        signal = raidmode.note_message(CHAT_ID, SETTINGS, now=500.0 + i)
    assert signal.triggered is True
    assert "3 messages within 10s" in signal.reason
    assert raidmode.release(CHAT_ID, actor="admin:1") is True
    assert raidmode.release(CHAT_ID, actor="admin:1") is False


def test_unverified_sender_is_contained_during_a_raid():
    raidmode.engage(CHAT_ID, "test raid", SETTINGS)
    action = decide.decide(ctx(text="gm"), policy(dry_run=False))
    assert action is not None
    assert action.rule == "raid_containment"
    assert action.delete is True
    assert "not verified" in action.reason


def test_verified_member_keeps_talking_during_a_raid():
    raidmode.engage(CHAT_ID, "test raid", SETTINGS)
    state.mark_verified(CHAT_ID, 7)
    assert decide.decide(ctx(text="gm"), policy(dry_run=False)) is None


def test_trusted_member_keeps_talking_during_a_raid():
    raidmode.engage(CHAT_ID, "test raid", SETTINGS)
    trust.trust(CHAT_ID, 7)
    assert decide.decide(ctx(text="gm"), policy(dry_run=False)) is None


def test_modraid_requires_admin():
    update = FakeUpdate(
        chat=FakeChat(CHAT_ID), user=FakeUser(7), message=FakeMessage(text="/modraid off")
    )
    context = SimpleNamespace(bot=FakeBot(), args=["off"])
    asyncio.run(raidmode.cmd_modraid(update, context))
    assert update.message.replies == ["⛔ Chat admins only."]
    assert raidmode.is_active(CHAT_ID) is False


def test_settings_resolution_order_is_env_then_chat_block(monkeypatch):
    monkeypatch.setenv("RUGMUNCH_MOD_RAID_JOINS", "12")
    assert guard_settings.from_env().raid_joins == 12
    assert guard_settings.for_chat(CHAT_ID).raid_joins == 12
    assert guard_settings.from_env().raid_window_s == GuardSettings().raid_window_s
