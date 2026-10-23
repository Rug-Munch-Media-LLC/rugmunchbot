"""Core moderation engine tests — offline, fake client, no network."""

from __future__ import annotations

import asyncio

import pytest

from rugmunchbot.community.moderation import (
    MessageEvent,
    ModerationClient,
    ModerationEngine,
    Policy,
)


class FakeClient:
    """Records every action instead of talking to Telegram."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.calls.append(("delete", chat_id, message_id))

    async def restrict_member(self, chat_id: int, user_id: int, until_date: int) -> None:
        self.calls.append(("mute", chat_id, user_id, until_date))

    async def ban_member(self, chat_id: int, user_id: int) -> None:
        self.calls.append(("ban", chat_id, user_id))

    async def send_message(self, chat_id: int, text: str) -> None:
        self.calls.append(("say", chat_id, text))


def _engine(**kwargs) -> ModerationEngine:
    kwargs.setdefault("clock", lambda: 1000.0)
    return ModerationEngine(**kwargs)


def _ev(text: str = "", **kwargs) -> MessageEvent:
    kwargs.setdefault("chat_id", -100)
    kwargs.setdefault("user_id", 7)
    kwargs.setdefault("message_id", 11)
    return MessageEvent(text=text, **kwargs)


def test_policy_defaults_are_inert() -> None:
    summary = _engine().policy_summary()
    assert summary["dry_run"] is True
    assert summary["auto_ban"] is False
    assert summary["trusted"] == 0


def test_fake_client_satisfies_protocol() -> None:
    assert isinstance(FakeClient(), ModerationClient)


def test_phishing_link_flagged_but_only_audited() -> None:
    engine, client = _engine(), FakeClient()
    verdict = engine.evaluate(_ev("claim now at https://solana-claim-rewards.xyz/airdrop"))
    assert verdict.level == "warn"
    assert any(r.startswith("phishing_link:") for r in verdict.reasons)
    assert "phishing_link:bit.ly" in engine.inspect(_ev("alpha https://bit.ly/abc123"))
    assert asyncio.run(engine.enforce(_ev(), verdict, client)) == "dry_run"
    assert client.calls == []


def test_trusted_host_is_not_phishing() -> None:
    engine = _engine(policy=Policy(trusted_hosts=("rugmunch.io",)))
    assert engine.inspect(_ev("docs at https://rugmunch.io/scanner")) == []


def test_pump_shilling_flagged() -> None:
    assert "pump_shilling" in _engine().inspect(_ev("100x guaranteed, buy now"))


def test_wallet_address_spam_flagged() -> None:
    text = "0x" + "a" * 40 + " and 0x" + "b" * 40
    assert "wallet_address_spam" in _engine().inspect(_ev(text))


def test_single_address_in_short_message_flagged() -> None:
    assert "wallet_address_spam" in _engine().inspect(_ev("send to 0x" + "c" * 40))


def test_mention_raid_flagged() -> None:
    assert "mention_raid:9" in _engine().inspect(_ev("gm", mentions=tuple(range(9))))


def test_new_account_only_when_configured() -> None:
    ev = _ev("hello", account_age_days=1)
    assert _engine().inspect(ev) == []
    configured = _engine(policy=Policy(new_account_days=3))
    assert "new_account:1d" in configured.inspect(ev)


def test_forwarded_new_account_flagged() -> None:
    engine = _engine(policy=Policy(new_account_days=3))
    reasons = engine.inspect(_ev("hi", forwarded=True, account_age_days=0))
    assert "forward_raid" in reasons


def test_duplicate_spam_escalates() -> None:
    engine = _engine(policy=Policy(duplicate_threshold=2, strikes_mute=3))
    first = engine.evaluate(_ev("buy my coin"))
    second = engine.evaluate(_ev("buy my coin"))
    assert "duplicate_spam" not in first.reasons
    assert "duplicate_spam" in second.reasons
    assert second.strikes == 1


def test_trusted_user_bypassed() -> None:
    engine = _engine(trusted=[7])
    verdict = engine.evaluate(_ev("100x guaranteed https://bit.ly/x"))
    assert verdict.level == "ok"
    assert engine.is_trusted(7)


def test_admin_event_bypassed() -> None:
    verdict = _engine().evaluate(_ev("100x guaranteed", is_admin=True))
    assert verdict.level == "ok"


def test_escalation_warn_then_mute() -> None:
    engine = _engine(policy=Policy(strikes_mute=2, strikes_ban=5, allow_auto_ban=True))
    assert engine.evaluate(_ev("100x")).level == "warn"
    assert engine.evaluate(_ev("100x")).level == "mute"


def test_auto_ban_capped_without_config() -> None:
    engine = _engine(policy=Policy(strikes_mute=2, strikes_ban=2))
    engine.evaluate(_ev("100x then 100x"))
    verdict = engine.evaluate(_ev("100x then 100x"))
    assert verdict.level == "mute"
    assert verdict.capped is True


def test_auto_ban_when_enabled() -> None:
    engine = _engine(policy=Policy(strikes_mute=2, strikes_ban=2, allow_auto_ban=True))
    engine.evaluate(_ev("100x then 100x"))
    assert engine.evaluate(_ev("100x then 100x")).level == "ban"


def test_enforce_live_mutes_and_deletes() -> None:
    engine = _engine(policy=Policy(dry_run=False, strikes_mute=1, strikes_ban=9))
    ev = _ev("100x guaranteed")
    verdict = engine.evaluate(ev)
    assert verdict.level == "mute"
    assert asyncio.run(engine.enforce(ev, verdict, FakeClient())) == "mute"


def test_enforce_live_ban() -> None:
    engine = _engine(
        policy=Policy(dry_run=False, strikes_mute=1, strikes_ban=1, allow_auto_ban=True)
    )
    client = FakeClient()
    ev = _ev("100x guaranteed")
    verdict = engine.evaluate(ev)
    assert asyncio.run(engine.enforce(ev, verdict, client)) == "ban"
    assert ("delete", -100, 11) in client.calls
    assert ("ban", -100, 7) in client.calls


def test_manual_trust_untrust_pardon() -> None:
    engine = _engine(policy=Policy(dry_run=False, strikes_mute=9, strikes_ban=9))
    engine.evaluate(_ev("100x"))
    assert engine.status(-100)["strikes"] == {7: 1}
    assert asyncio.run(engine.enforce_manual("pardon", -100, 7)) == "recorded"
    assert engine.status(-100)["strikes"] == {}
    asyncio.run(engine.enforce_manual("trust", -100, 7))
    assert engine.is_trusted(7)
    asyncio.run(engine.enforce_manual("untrust", -100, 7))
    assert not engine.is_trusted(7)


def test_manual_action_recorded_in_dry_run() -> None:
    engine, client = _engine(), FakeClient()
    assert asyncio.run(engine.enforce_manual("mute", -100, 7, client, reason="spam")) == "dry_run"
    assert client.calls == []
    assert engine.actions(-100)[0]["level"] == "mute"


def test_unknown_manual_action_rejected() -> None:
    with pytest.raises(ValueError, match="nuke"):
        asyncio.run(_engine().enforce_manual("nuke", -100, 7))


def test_audit_keeps_newest_first() -> None:
    engine = _engine()
    engine.evaluate(_ev("100x", message_id=1))
    engine.evaluate(_ev("100x", message_id=2))
    rows = engine.actions(-100)
    assert rows[0]["message_id"] == 2
    assert rows[0]["dry_run"] is True
