"""Action execution: dry-run silence, durations, propagation, audit actor."""

from __future__ import annotations

import pytest

from rugmunchbot.moderation import actions, audit, state
from rugmunchbot.moderation.harness_fakes import CHAT_ID, FakeBot, ctx, policy
from rugmunchbot.moderation.models import Action


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    original = audit.AUDIT_PATH
    state.reset()
    audit.use_audit_path(tmp_path / "actions.jsonl")
    yield
    audit.use_audit_path(original)
    state.reset()


class RecordingBot:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return _record


def _ctx(message_id=11, user_id=7):
    return ctx("bad", user_id=user_id, message_id=message_id)


async def test_dry_run_calls_nothing_and_still_audits(tmp_path):
    bot = FakeBot()
    row = await actions.execute(bot, _ctx(), policy(), Action("promo", "delete", "pitch"))
    assert bot.calls == []
    assert row["dry_run"] is True
    assert row["applied"] is False
    assert audit.tail()[-1]["rule"] == "promo"


async def test_live_delete_uses_message_id_and_records_actor():
    bot = RecordingBot()
    pol = policy(dry_run=False)
    row = await actions.execute(
        bot, _ctx(), pol, Action("scam_link", "delete", "scam", delete=True), actor="auto"
    )
    assert ("delete_message", {"chat_id": CHAT_ID, "message_id": 11}) in [
        (n, k) for n, _, k in bot.calls
    ]
    assert row["applied"] is True
    assert row["actor"] == "auto"


async def test_mute_carries_until_date_and_default_minutes():
    bot = RecordingBot()
    pol = policy(dry_run=False, mute_minutes=15)
    await actions.execute(bot, _ctx(), pol, Action("user_velocity", "mute", "fast"))
    name, _, kwargs = bot.calls[0]
    assert name == "restrict_chat_member"
    assert kwargs["until_date"] is not None
    assert kwargs["permissions"].can_send_messages is False


async def test_ban_uses_configured_duration_and_propagates():
    bot = RecordingBot()
    peer = -1009
    pol = policy(dry_run=False, ban_minutes=60, propagate_ban=True, propagate_chats=[peer])
    row = await actions.execute(bot, _ctx(), pol, Action("impersonation", "ban", "impostor"))
    bans = [k for n, _, k in bot.calls if n == "ban_chat_member"]
    assert bans[0]["chat_id"] == CHAT_ID
    assert bans[0]["until_date"] is not None
    assert bans[1]["chat_id"] == peer
    assert row["applied"] is True
    assert audit.tail(peer, 1)[0]["rule"] == "ban_propagation"


async def test_lift_is_a_noop_in_dry_run():
    assert await actions.lift(FakeBot(), -1001, 7, policy()) is False
