"""Entrance guard: join heuristics, explainability, no implicit admin bypass."""

from __future__ import annotations

import asyncio
import os

import pytest

from rugmunchbot.moderation import audit, guard_settings, quarantine, raidmode, state, trust
from rugmunchbot.moderation.entrance import JoinRisk, assess_join
from rugmunchbot.moderation.guard_settings import GuardSettings
from rugmunchbot.moderation.harness_fakes import CHAT_ID, FakeBot, ctx, policy
from rugmunchbot.moderation.policy import ChatPolicy

CLEAN_ID = 1_000_001
NEW_ID = 7_900_000_000


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


def test_clean_member_only_has_to_pass_the_captcha():
    risk = assess_join(user_id=CLEAN_ID, username="alice", display_name="Alice")
    assert risk.action == "verify"
    assert risk.score < GuardSettings().quarantine_score
    assert risk.explained == "no signals"


def test_bot_join_is_quarantined_not_admitted():
    risk = assess_join(user_id=CLEAN_ID, display_name="SpamBot", is_bot=True)
    assert risk.action == "quarantine"
    assert risk.reasons == ("bot account",)


def test_new_account_without_username_crosses_the_threshold():
    risk = assess_join(user_id=NEW_ID, username="", display_name="Airdrop Claim")
    assert risk.action == "quarantine"
    assert "user id at/above new-account watermark" in risk.reasons
    assert "no @username" in risk.reasons
    assert "scam/authority keyword in name" in risk.reasons


def test_thresholds_come_from_env_not_code(monkeypatch):
    monkeypatch.setenv("RUGMUNCH_MOD_QUARANTINE_SCORE", "200")
    monkeypatch.setenv("RUGMUNCH_MOD_SCORE_NEW_ACCOUNT", "10")
    settings = guard_settings.from_env()
    assert settings.quarantine_score == 200
    risk = assess_join(user_id=NEW_ID, username="bob", display_name="Bob", settings=settings)
    assert risk.action == "verify"


def test_bot_quarantine_can_be_disabled():
    risk = assess_join(
        user_id=1, display_name="x", is_bot=True, settings=GuardSettings(bot_quarantine=False)
    )
    assert risk.action == "allow"


def test_machine_looking_handle_scores_but_does_not_ban():
    risk = assess_join(user_id=CLEAN_ID, username="bot", display_name="user12345678")
    assert "machine-looking handle" in risk.reasons
    assert risk.action in {"verify", "quarantine"}
    assert risk.action != "ban"


def test_admins_have_no_implicit_bypass():
    default = ChatPolicy.from_dict(CHAT_ID, {})
    assert default.exempt_admins is False
    assert default.is_exempt(ctx(is_admin=True)) is False
    opted_in = ChatPolicy.from_dict(CHAT_ID, {"exempt_admins": True})
    assert opted_in.is_exempt(ctx(is_admin=True)) is True


def test_false_positive_hold_is_explained_and_reversible():
    bot = FakeBot()
    risk = JoinRisk(user_id=7, score=80, reasons=("bot account",), action="quarantine")
    entry = asyncio.run(
        quarantine.hold(
            bot,
            CHAT_ID,
            7,
            risk,
            GuardSettings(),
            policy=policy(dry_run=False),
            username="appeal-me",
        )
    )
    assert entry["reason"] == "bot account"
    assert entry["action_id"]
    released = asyncio.run(
        quarantine.release(bot, CHAT_ID, 7, actor="admin:1", reason="verified human")
    )
    assert released is not None
    assert released["rule"] == "quarantine_release"
    assert released["applied"] is True
    rows = audit.tail(CHAT_ID, 5)
    assert {r["rule"] for r in rows} >= {"entrance_quarantine", "quarantine_release"}
    assert quarantine.pending(CHAT_ID) == []


def test_trusted_user_skips_entrance_quarantine_only():
    trust.trust(CHAT_ID, 42)
    assert trust.is_trusted(CHAT_ID, 42) is True
    assert trust.is_trusted(CHAT_ID, 43) is False
    trust.untrust(CHAT_ID, 42)
    assert trust.is_trusted(CHAT_ID, 42) is False
