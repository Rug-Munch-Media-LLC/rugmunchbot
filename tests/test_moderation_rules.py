"""Unit tests for the moderation decision core (no network, no Telegram)."""

from __future__ import annotations

import pytest

from rugmunchbot.moderation import addresses, config, links, state
from rugmunchbot.moderation.decide import decide
from rugmunchbot.moderation.models import AddressVerdict, MsgContext


@pytest.fixture(autouse=True)
def _clean_state():
    state.reset()
    yield
    state.reset()


def _policy(**overrides):
    return config.ChatPolicy.from_dict(-1001, overrides)


def _ctx(text: str = "", **kw) -> MsgContext:
    base = {
        "chat_id": -1001,
        "chat_title": "t",
        "user_id": 7,
        "username": "u",
        "display_name": "n",
        "text": text,
    }
    base.update(kw)
    return MsgContext(**base)


def test_defaults_are_dry_run_and_never_ban():
    policy = _policy()
    assert policy.dry_run is True
    assert policy.user_velocity_action == "mute"
    assert "ban" not in {
        policy.link_action,
        policy.promo_action,
        policy.dup_action,
        policy.nsfw_action,
    }


def test_unconfigured_chat_is_unguarded(tmp_path, monkeypatch):
    cfg = tmp_path / "m.json"
    cfg.write_text('{"chats": {}}')
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    config._cache.clear()
    assert config.load_chat_policy(-1001) is None


def test_scam_link_wins_over_promo_and_allowlist():
    ctx = _ctx(
        "claim https://claim-airdrop.xyz/x 100x guaranteed", urls=("https://claim-airdrop.xyz/x",)
    )
    action = decide(ctx, _policy())
    assert action is not None
    assert action.rule == "scam_link"
    assert action.action == "delete"


def test_allowlisted_docs_link_is_not_flagged():
    ctx = _ctx("read https://docs.rugmunch.io/api", urls=("https://docs.rugmunch.io/api",))
    assert decide(ctx, _policy()) is None


def test_unlisted_link_warns_without_delete():
    ctx = _ctx("see https://random.xyz/a", urls=("https://random.xyz/a",))
    action = decide(ctx, _policy())
    assert action is not None
    assert action.rule == "unlisted_link"
    assert action.action == "warn"
    assert action.delete is False


def test_repeat_message_needs_configured_count():
    policy = _policy(dup_count=3)
    for _ in range(2):
        assert decide(_ctx("same text"), policy) is None
    action = decide(_ctx("same text"), policy)
    assert action is not None
    assert action.rule == "repeat_message"


def test_exempt_user_and_admin_are_never_actioned():
    policy = _policy()
    for _ in range(5):
        assert decide(_ctx("100x guaranteed", is_admin=True), policy) is None
    assert decide(_ctx("100x guaranteed", user_id=999), _policy(exempt_user_ids=[999])) is None


def test_address_verdict_avoid_escalates_to_delete():
    verdicts = {
        "0x1": AddressVerdict(
            address="0x1", chain="ethereum", risk_level="critical", safety_score=3, verdict="avoid"
        )
    }
    action = decide(_ctx("buy 0x1"), _policy(), verdicts)
    assert action is not None
    assert action.rule == "address_risk"
    assert action.delete is True


def test_clean_verdict_is_ignored():
    verdicts = {"0x1": AddressVerdict(address="0x1", verdict="ok", safety_score=88)}
    assert decide(_ctx("chart 0x1"), _policy(), verdicts) is None


def test_address_extraction_validates_and_dedupes():
    text = (
        "0x1111111111111111111111111111111111111111 and 0x1111111111111111111111111111111111111111"
    )
    assert addresses.extract_addresses(text) == ["0x1111111111111111111111111111111111111111"]


def test_verdict_mapping_thresholds():
    assert addresses.verdict_from_payload("0x1", {"safety_score": 5}).verdict == "avoid"
    assert addresses.verdict_from_payload("0x1", {"risk_level": "high"}).verdict == "caution"
    assert addresses.verdict_from_payload("0x1", {"safety_score": 90}).verdict == "ok"
    assert addresses.verdict_from_payload("0x1", None).verdict == "unknown"


def test_extract_urls_includes_hidden_entity_targets():
    urls = links.extract_urls("hello", ("https://t.me/+secret",))
    assert urls == ("https://t.me/+secret",)


def test_impostor_name_is_flagged_not_admin():
    action = decide(_ctx("hi", display_name="RugMunch Support"), _policy())
    assert action is not None
    assert action.rule == "impersonation"
