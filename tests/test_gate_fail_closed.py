"""Fail-closed contracts for the entrance gate.

Every test here pins a path that used to fail *open*: a quarantine that
could not mute, a forged or stale captcha callback, an audit write that
vanished, and containment state lost on restart.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from rugmunchbot.moderation import (
    audit,
    captcha,
    captcha_verify,
    config,
    contain,
    entrance,
    guard_settings,
    persist,
    quarantine,
    register,
    state,
)
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    FakeBot,
    FakeChat,
    FakeQuery,
    FakeQueue,
    FakeUpdate,
    FakeUser,
    policy,
    rig_context,
)

USER = 601


class RefusingBot(FakeBot):
    """A bot the chat never granted restrict rights to."""

    def __init__(self) -> None:
        super().__init__()
        self.restricts = 0

    async def restrict_chat_member(self, **kwargs):
        self.restricts += 1
        raise RuntimeError("not enough rights")


class RecordingApp:
    def __init__(self) -> None:
        self.handlers: list = []

    def add_handler(self, handler, group=0):
        self.handlers.append((group, getattr(handler, "callback", None), handler))


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    original_state, original_audit, original_cfg = (
        persist.PATH,
        audit.AUDIT_PATH,
        config.CONFIG_PATH,
    )
    persist.use_path(tmp_path / "moderation_state.json")
    audit.use_audit_path(tmp_path / "audit.jsonl")
    state.reset()
    quarantine.reset()
    captcha.reset()
    yield
    state.reset()
    quarantine.reset()
    captcha.reset()
    persist.use_path(original_state)
    audit.use_audit_path(original_audit)
    config.use_config_path(original_cfg)


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "moderation.json"
    path.write_text(
        json.dumps(
            {
                "chats": {
                    str(CHAT_ID): {"dry_run": False, "captcha_type": "button"},
                    "-100999": {"dry_run": True, "captcha_type": "button"},
                }
            }
        )
    )
    config.use_config_path(path)
    return path


def callback(user_id: int = USER):
    query = FakeQuery(captcha.callback_data(CHAT_ID, user_id), FakeUser(user_id))
    update = FakeUpdate(chat=FakeChat(CHAT_ID), user=FakeUser(user_id), callback_query=query)
    return query, update


# --------------------------------------------------------------- file cap
def test_captcha_layer_is_under_the_two_hundred_line_cap():
    for module in (captcha, contain):
        source = Path(module.__file__).read_text().splitlines()
        assert len(source) < 200, f"{module.__name__} grew to {len(source)} lines"


# ------------------------------------------------- forged / stale callback
def test_callback_without_a_pending_challenge_cannot_unmute(cfg):
    bot = FakeBot()
    query, update = callback(601)
    assert asyncio.run(
        captcha_verify.handle_captcha_callback(update, rig_context(bot, FakeQueue()))
    )
    assert state.is_verified(CHAT_ID, 601) is False
    assert "restrict_chat_member" not in bot.calls
    assert query.answers == ["This challenge expired. Ask an admin to release you."]
    assert audit.tail(CHAT_ID, 1)[0]["rule"] == "captcha_stale"


def test_expired_challenge_is_rejected(cfg):
    captcha.register_pending(CHAT_ID, 602, 30, now=time.time() - 120)
    _query, update = callback(602)
    asyncio.run(captcha_verify.handle_captcha_callback(update, rig_context(FakeBot(), FakeQueue())))
    assert state.is_verified(CHAT_ID, 602) is False
    assert audit.tail(CHAT_ID, 1)[0]["rule"] == "captcha_stale"


def test_live_challenge_verifies_and_is_consumed(cfg):
    captcha.register_pending(CHAT_ID, 603, 90)
    _query, update = callback(603)
    asyncio.run(captcha_verify.handle_captcha_callback(update, rig_context(FakeBot(), FakeQueue())))
    assert state.is_verified(CHAT_ID, 603) is True
    assert captcha.has_pending(CHAT_ID, 603) is False


# ------------------------------------------------------------------ audit
def test_audit_record_does_not_swallow_oserror(tmp_path):
    audit.use_audit_path(tmp_path)  # a directory: open() raises IsADirectoryError
    entry = audit.build_entry(
        action_id="x",
        chat_id=CHAT_ID,
        chat_title="",
        user_id=USER,
        username="",
        rule="r",
        action="a",
        reason="reason",
        matched="",
        dry_run=False,
        applied=True,
    )
    with pytest.raises(IsADirectoryError):
        audit.record(entry)


def test_contain_record_reports_a_failed_write(tmp_path):
    audit.use_audit_path(tmp_path)
    assert contain.record(CHAT_ID, USER, "r", "a", "reason", False) is False


# ---------------------------------------------------- quarantine escalation
def test_quarantine_restrict_failure_retries_then_escalates():
    bot = RefusingBot()
    settings = guard_settings.GuardSettings()
    risk = entrance.JoinRisk(user_id=701, score=80, reasons=("new account",), action="quarantine")
    entry = asyncio.run(
        quarantine.hold(bot, CHAT_ID, 701, risk, settings, policy=policy(dry_run=False))
    )
    assert bot.restricts == 2
    assert entry["applied"] is False
    assert entry["error"] == "restrict: RuntimeError"
    assert [row["user_id"] for row in quarantine.pending(CHAT_ID)] == [701]
    rules = [row["rule"] for row in audit.tail(CHAT_ID, 3)]
    assert "entrance_quarantine_failed" in rules
    assert "send_message" in bot.calls


def test_quarantine_hold_reports_success_when_the_mute_lands():
    bot = FakeBot()
    settings = guard_settings.GuardSettings()
    risk = entrance.JoinRisk(user_id=702, score=70, reasons=("bot pattern",), action="quarantine")
    entry = asyncio.run(
        quarantine.hold(bot, CHAT_ID, 702, risk, settings, policy=policy(dry_run=False))
    )
    assert entry["applied"] is True
    assert bot.calls.count("restrict_chat_member") == 1


# ------------------------------------------------------- restart recovery
def test_quarantine_pending_survives_restart():
    risk = entrance.JoinRisk(user_id=801, score=60, reasons=("no username",), action="quarantine")
    asyncio.run(
        quarantine.hold(
            FakeBot(), CHAT_ID, 801, risk, guard_settings.GuardSettings(), policy=policy()
        )
    )
    quarantine.reset()
    assert quarantine.recover() == 1
    assert quarantine.pending(CHAT_ID)[0]["user_id"] == 801


def test_verified_member_survives_restart():
    state.mark_verified(CHAT_ID, 802)
    state.reset()
    assert state.recover() == 1
    assert state.is_verified(CHAT_ID, 802) is True


def test_gate_pending_survives_restart_and_rearms_the_timeout(cfg):
    captcha.register_pending(CHAT_ID, 803, 90)
    captcha.reset()
    queue = FakeQueue()
    assert captcha.recover(SimpleNamespace(bot=FakeBot(), job_queue=queue)) == 1
    assert captcha.has_pending(CHAT_ID, 803) is True
    assert [job.name for job in queue.jobs] == [f"modcap:{CHAT_ID}:803"]


def test_expired_gate_pending_is_not_restored(cfg):
    captcha.register_pending(CHAT_ID, 804, 30, now=time.time() - 120)
    captcha.reset()
    queue = FakeQueue()
    assert captcha.recover(SimpleNamespace(bot=FakeBot(), job_queue=queue)) == 0
    assert queue.jobs == []


def test_register_recovers_state_on_a_bare_app(cfg):
    captcha.register_pending(CHAT_ID, 805, 90)
    app = RecordingApp()
    register.register_moderation(app)
    assert captcha.has_pending(CHAT_ID, 805) is True
