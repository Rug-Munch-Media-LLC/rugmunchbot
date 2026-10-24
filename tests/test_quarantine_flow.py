"""Quarantine channel, dry-run safety, release audit and permission checks."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from rugmunchbot.moderation import audit, quarantine, raidmode, state, trust
from rugmunchbot.moderation.entrance import JoinRisk
from rugmunchbot.moderation.guard_settings import GuardSettings
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    FakeBot,
    FakeChat,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    policy,
)

RISK = JoinRisk(user_id=7, score=80, reasons=("bot account",), action="quarantine")
CHANNEL = -1009999999999


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


def test_live_hold_restricts_and_posts_to_the_quarantine_channel():
    bot = FakeBot()
    entry = asyncio.run(
        quarantine.hold(
            bot,
            CHAT_ID,
            7,
            RISK,
            GuardSettings(quarantine_channel_id=CHANNEL),
            policy=policy(dry_run=False),
            username="spam",
        )
    )
    assert "restrict_chat_member" in bot.calls
    assert "send_message" in bot.calls
    assert entry["applied"] is True
    assert entry["action_id"]
    pending = quarantine.pending(CHAT_ID)
    assert [p["user_id"] for p in pending] == [7]
    assert pending[0]["reasons"] == ["bot account"]


def test_dry_run_records_but_touches_nothing():
    bot = FakeBot()
    entry = asyncio.run(
        quarantine.hold(
            bot,
            CHAT_ID,
            7,
            RISK,
            GuardSettings(quarantine_channel_id=CHANNEL),
            policy=policy(dry_run=True),
        )
    )
    assert bot.calls == []
    assert entry["dry_run"] is True
    assert entry["applied"] is False
    assert quarantine.pending(CHAT_ID)[0]["action_id"] == entry["action_id"]


def test_release_unmutes_and_records_who_did_it():
    bot = FakeBot()
    asyncio.run(
        quarantine.hold(bot, CHAT_ID, 7, RISK, GuardSettings(), policy=policy(dry_run=False))
    )
    entry = asyncio.run(
        quarantine.release(bot, CHAT_ID, 7, actor="admin:99", reason="appeal accepted")
    )
    assert entry is not None
    assert entry["actor"] == "admin:99"
    assert entry["reason"] == "appeal accepted"
    assert entry["held_action_id"]
    assert bot.calls.count("restrict_chat_member") == 2
    assert quarantine.pending(CHAT_ID) == []


def test_release_without_a_hold_is_a_noop():
    assert asyncio.run(quarantine.release(FakeBot(), CHAT_ID, 7, actor="admin:99")) is None


def test_modrelease_requires_admin():
    update = FakeUpdate(
        chat=FakeChat(CHAT_ID), user=FakeUser(7), message=FakeMessage(text="/modrelease 7")
    )
    context = SimpleNamespace(bot=FakeBot(), args=["7"])
    asyncio.run(quarantine.cmd_modrelease(update, context))
    assert update.message.replies == ["⛔ Chat admins only."]


def test_hold_caps_pending_memory():
    bot = FakeBot()
    for user_id in range(quarantine.MAX_PENDING + 5):
        asyncio.run(
            quarantine.hold(
                bot, CHAT_ID, user_id, RISK, GuardSettings(), policy=policy(dry_run=True)
            )
        )
    assert len(quarantine.pending(CHAT_ID)) == quarantine.MAX_PENDING
