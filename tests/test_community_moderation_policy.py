"""Per-chat policy load/save and dry-run vs enforced behaviour of the engine.

Redis is the injected fake — nothing here touches a network or patches a
module global.
"""

from __future__ import annotations

from moderation_fakes import CHAT, LIVE, OTHER, USER, FakeClient, FakeRedis, event

from rugmunchbot.community.moderation import ModerationEngine
from rugmunchbot.community.moderation_config import (
    load_policy,
    policy_key,
    save_policy,
    sync_policy,
)
from rugmunchbot.community.moderation_models import Policy
from rugmunchbot.community.moderation_policy import policy_from_mapping, policy_to_mapping


# -- load / save -----------------------------------------------------------
async def test_missing_or_unreachable_redis_loads_inert_defaults() -> None:
    for client in (FakeRedis(), FakeRedis(broken=True)):
        policy = await load_policy(CHAT, client=client)
        assert policy.dry_run is True
        assert policy.allow_auto_ban is False
        assert policy.strikes_ban == 5


async def test_save_then_load_round_trips() -> None:
    redis = FakeRedis()
    assert await save_policy(CHAT, LIVE, client=redis) is True
    stored = await load_policy(CHAT, client=redis)
    assert stored.dry_run is False
    assert stored.allow_auto_ban is True
    assert stored.strikes_ban == 2
    assert policy_key(CHAT) in redis.values
    assert await save_policy(CHAT, LIVE, client=FakeRedis(broken=True)) is False


async def test_corrupt_document_falls_back_to_inert() -> None:
    redis = FakeRedis()
    redis.values[policy_key(CHAT)] = "{not json"
    policy = await load_policy(CHAT, client=redis)
    assert policy.dry_run is True
    assert policy.allow_auto_ban is False


async def test_hostile_values_are_clamped_and_junk_hosts_dropped() -> None:
    policy = policy_from_mapping(
        {
            "dry_run": "nonsense",
            "allow_auto_ban": "nonsense",
            "strikes_mute": 0,
            "strikes_ban": -5,
            "mute_seconds": 10**9,
            "duplicate_threshold": "abc",
            "trusted_hosts": ["OK.example", "bad host", 7, "ok.example"],
        }
    )
    assert policy.dry_run is True
    assert policy.allow_auto_ban is False
    assert policy.strikes_mute == 1
    assert policy.strikes_ban >= policy.strikes_mute
    assert policy.mute_seconds == 2_592_000
    assert policy.duplicate_threshold == 3
    assert policy.trusted_hosts == ("ok.example",)


async def test_serialised_policy_survives_a_round_trip() -> None:
    restored = policy_from_mapping(policy_to_mapping(LIVE))
    assert restored.dry_run is False
    assert restored.strikes_mute == LIVE.strikes_mute
    assert restored.strikes_ban == LIVE.strikes_ban


# -- engine wiring ---------------------------------------------------------
async def test_sync_policy_installs_only_for_that_chat() -> None:
    redis = FakeRedis()
    await save_policy(CHAT, LIVE, client=redis)
    engine = ModerationEngine(clock=lambda: 1000.0)
    await sync_policy(engine, CHAT, client=redis)
    assert engine.policy_for(CHAT).dry_run is False
    assert engine.policy_for(OTHER).dry_run is True


async def test_dry_run_chat_never_calls_the_client() -> None:
    engine = ModerationEngine(clock=lambda: 1000.0)
    client = FakeClient()
    verdict = engine.evaluate(event())
    assert verdict.level == "warn"
    assert verdict.dry_run is True
    assert await engine.enforce(event(), verdict, client) == "dry_run"
    assert client.calls == []


async def test_enforced_chat_bans_after_reaching_the_threshold() -> None:
    engine = ModerationEngine(clock=lambda: 1000.0)
    engine.use_policy(CHAT, LIVE)
    client = FakeClient()
    engine.evaluate(event())
    verdict = engine.evaluate(event())
    assert verdict.level == "ban"
    assert await engine.enforce(event(), verdict, client) == "ban"
    assert ("delete", CHAT, 77) in client.calls
    assert ("ban", CHAT, USER) in client.calls


async def test_auto_ban_off_caps_the_escalation_at_a_mute() -> None:
    engine = ModerationEngine(clock=lambda: 1000.0)
    engine.use_policy(CHAT, Policy(dry_run=False, strikes_mute=1, strikes_ban=2))
    client = FakeClient()
    engine.evaluate(event())
    verdict = engine.evaluate(event())
    assert verdict.level == "mute"
    assert verdict.capped is True
    assert await engine.enforce(event(), verdict, client) == "mute"
    assert not [call for call in client.calls if call[0] == "ban"]
