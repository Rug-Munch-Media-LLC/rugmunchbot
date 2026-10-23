"""Community settings — round-trip, validation and audit (no network).

The Redis client is injected through the package's DI seam; nothing here
patches module globals or touches the network. Every write goes through the
real pydantic models, so a value that passes here is a value the bot accepts.
"""

from __future__ import annotations

import json

import pytest

from rugmunchbot.community.api import is_feature_enabled
from rugmunchbot.community.audit import get_audit_log
from rugmunchbot.community.store import load_settings, settings_key
from rugmunchbot.community.validation import update_setting

CHAT = -1001234567890
ADMIN = 424242
EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"
SOL = "So11111111111111111111111111111111111111112"


class FakeRedis:
    """Minimal async Redis: get/set/delete + the list ops the audit trail uses."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        for key in keys:
            self.values.pop(key, None)
            self.lists.pop(key, None)
        return len(keys)

    async def lpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> bool:
        self.lists[key] = self.lists.get(key, [])[start : end + 1]
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self.lists.get(key, [])[start : end + 1]


class BrokenRedis:
    """Every operation fails — stands in for a Redis outage."""

    async def get(self, key: str) -> str | None:
        raise RuntimeError("redis down")

    async def set(self, key: str, value: str) -> bool:
        raise RuntimeError("redis down")

    async def delete(self, *keys: str) -> int:
        raise RuntimeError("redis down")


@pytest.fixture
def redis() -> FakeRedis:
    return FakeRedis()


async def test_round_trip_persists_every_section(redis: FakeRedis) -> None:
    token = {"chain": "ethereum", "address": EVM, "symbol": "pepe", "name": "Pepe"}
    assert await update_setting(CHAT, ADMIN, "token", token, client=redis) == (True, None)
    assert (await update_setting(CHAT, ADMIN, "amounts", "0.5,2", sub_key="buy", client=redis))[0]
    emoji = {"emoji": "🚀", "label": "Ape"}
    assert (await update_setting(CHAT, ADMIN, "emojis", emoji, sub_key="sell", client=redis))[0]
    assert (
        await update_setting(CHAT, ADMIN, "moderation", {"max_links_per_message": 1}, client=redis)
    )[0]
    assert (
        await update_setting(
            CHAT, ADMIN, "voting", {"enabled": True, "quorum_pct": 25}, client=redis
        )
    )[0]
    assert (await update_setting(CHAT, ADMIN, "features", {"trading": False}, client=redis))[0]
    assert (await update_setting(CHAT, ADMIN, "admins", [ADMIN, 777], client=redis))[0]

    loaded = await load_settings(CHAT, redis)
    assert loaded.token is not None
    assert loaded.token.address == EVM.lower()
    assert loaded.token.symbol == "PEPE"
    assert loaded.amounts.buy == [0.5, 2.0]
    assert loaded.emojis.sell.label == "Ape"
    assert loaded.moderation.max_links_per_message == 1
    assert loaded.voting.enabled is True
    assert loaded.voting.quorum_pct == 25
    assert loaded.features.trading is False
    assert loaded.admins == [ADMIN, 777]
    assert loaded.updated_by == ADMIN

    stored = json.loads(redis.values[settings_key(CHAT)])
    assert "chat_id" not in stored
    assert stored["token"]["chain"] == "ethereum"
    assert await is_feature_enabled(CHAT, "trading", redis) is False


@pytest.mark.parametrize(
    ("key", "value", "sub_key", "fragment"),
    [
        (
            "token",
            {"chain": "dogecoin", "address": EVM, "symbol": "D", "name": "Doge"},
            None,
            "ethereum",
        ),
        (
            "token",
            {"chain": "ethereum", "address": "0xdeadbeef", "symbol": "X", "name": "X"},
            None,
            "40 hex",
        ),
        (
            "token",
            {"chain": "solana", "address": "0x1234", "symbol": "X", "name": "X"},
            None,
            "base58",
        ),
        ("amounts", "0.1,99999999", "buy", "between"),
        ("amounts", "abc", "sell", "not a number"),
        ("emojis", {"emoji": "A B"}, "buy", "whitespace"),
        ("emojis", {"emoji": "  "}, "buy", "cannot be empty"),
        ("moderation", {"nope": 1}, None, "unknown moderation field"),
        ("voting", {"quorum_pct": 900}, None, "less than or equal to 100"),
        ("admins", ["not-an-id"], None, "valid integer"),
        ("bogus", {}, None, "unknown setting key"),
    ],
)
async def test_invalid_values_are_rejected_and_not_persisted(
    redis: FakeRedis,
    key: str,
    value: object,
    sub_key: str | None,
    fragment: str,
) -> None:
    ok, error = await update_setting(CHAT, ADMIN, key, value, sub_key=sub_key, client=redis)
    assert ok is False
    assert error
    assert fragment in error
    assert settings_key(CHAT) not in redis.values


async def test_solana_token_is_accepted(redis: FakeRedis) -> None:
    token = {"chain": "solana", "address": SOL, "symbol": "wsol", "name": "Wrapped SOL"}
    assert await update_setting(CHAT, ADMIN, "token", token, client=redis) == (True, None)
    loaded = await load_settings(CHAT, redis)
    assert loaded.token is not None
    assert loaded.token.chain.value == "solana"


async def test_audit_records_old_and_new_values(redis: FakeRedis) -> None:
    old = {"chain": "bsc", "address": EVM, "symbol": "old", "name": "Old"}
    new = {"chain": "bsc", "address": EVM, "symbol": "new", "name": "New"}
    await update_setting(CHAT, ADMIN, "token", old, client=redis)
    await update_setting(CHAT, 999, "token", new, client=redis)

    entries = await get_audit_log(CHAT, client=redis)
    assert entries, "audit trail must record a successful change"
    latest = entries[0]
    assert latest.key == "token"
    assert latest.user_id == 999
    assert latest.new_value["symbol"] == "new"
    assert latest.old_value["symbol"] == "OLD"


async def test_redis_outage_degrades_to_defaults() -> None:
    broken = BrokenRedis()
    loaded = await load_settings(CHAT, broken)
    assert loaded.token is None
    ok, error = await update_setting(CHAT, ADMIN, "features", {"trading": True}, client=broken)
    assert ok is False
    assert error is not None
    assert "storage unavailable" in error
