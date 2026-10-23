"""``/setup`` admin commands — gating and dispatch (no Telegram network).

Telegram is stubbed: ``effective_chat``/``effective_user``/``reply_text`` are
mocks and ``get_chat_administrators`` returns what the test declares. The Redis
client arrives through ``Application.bot_data`` (the same DI seam production
uses when one is configured), so no module globals are patched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from rugmunchbot.community.store import COMMUNITY_CLIENT_KEY, load_settings, save_settings
from rugmunchbot.handlers.community_admin import cmd_setup

CHAT = -1009876543210
EVM = "0x6982508145454Ce325dDbE47a25d4ec3d2311933"


class FakeRedis:
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


def _update(user_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id, username="tester")
    update.effective_chat = MagicMock(id=CHAT, type="supergroup")
    update.effective_message = MagicMock(reply_text=AsyncMock())
    return update


def _ctx(
    redis: FakeRedis,
    args: list[str] | None = None,
    telegram_admins: tuple[int, ...] = (),
) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    ctx.bot_data = {COMMUNITY_CLIENT_KEY: redis}
    ctx.bot.get_chat_administrators = AsyncMock(
        return_value=[MagicMock(user=MagicMock(id=uid)) for uid in telegram_admins]
    )
    return ctx


def _replied(update: MagicMock) -> str:
    call = update.effective_message.reply_text.await_args
    assert call is not None, "handler did not reply"
    return str(call.args[0])


async def _make_admin(redis: FakeRedis, user_id: int) -> None:
    settings = await load_settings(CHAT, redis)
    await save_settings(settings.model_copy(update={"admins": [user_id]}), redis)


async def test_non_admin_is_denied_and_nothing_changes() -> None:
    redis = FakeRedis()
    update = _update(999)
    await cmd_setup(update, _ctx(redis, ["token", "bsc", EVM, "PEPE", "Pepe"]))

    assert "Admin only" in _replied(update)
    assert (await load_settings(CHAT, redis)).token is None
    assert redis.values == {}


async def test_explicit_admin_can_read_and_write() -> None:
    redis = FakeRedis()
    await _make_admin(redis, 555)
    update = _update(555)
    await cmd_setup(update, _ctx(redis, ["token", "ethereum", EVM, "PEPE", "Pepe Coin"]))

    assert "✅" in _replied(update)
    token = (await load_settings(CHAT, redis)).token
    assert token is not None
    assert (token.symbol, token.name) == ("PEPE", "Pepe Coin")


async def test_telegram_chat_admin_can_use_setup() -> None:
    redis = FakeRedis()
    update = _update(321)
    await cmd_setup(update, _ctx(redis, ["show"], telegram_admins=(321,)))

    assert "Community settings" in _replied(update)


async def test_admin_lookup_failure_denies() -> None:
    redis = FakeRedis()
    ctx = _ctx(redis, ["show"])
    ctx.bot.get_chat_administrators = AsyncMock(side_effect=RuntimeError("telegram down"))
    update = _update(321)
    await cmd_setup(update, ctx)

    assert "Admin only" in _replied(update)


async def test_invalid_token_reports_reason() -> None:
    redis = FakeRedis()
    await _make_admin(redis, 555)
    update = _update(555)
    await cmd_setup(update, _ctx(redis, ["token", "ethereum", "not-an-address", "PEPE", "Pepe"]))

    reply = _replied(update)
    assert "⛔" in reply
    assert "40 hex" in reply
    assert (await load_settings(CHAT, redis)).token is None


async def test_amounts_emoji_features_and_audit_round_trip() -> None:
    redis = FakeRedis()
    await _make_admin(redis, 42)

    await cmd_setup(_update(42), _ctx(redis, ["amounts", "buy", "0.25,1,5"]))
    await cmd_setup(_update(42), _ctx(redis, ["emoji", "buy", "🟢", "Ape"]))
    await cmd_setup(_update(42), _ctx(redis, ["features", "trading", "off"]))
    update = _update(42)
    await cmd_setup(update, _ctx(redis, ["audit"]))

    loaded = await load_settings(CHAT, redis)
    assert loaded.amounts.buy == [0.25, 1.0, 5.0]
    assert loaded.emojis.buy.emoji == "🟢"
    assert loaded.features.trading is False
    assert "amounts.buy" in _replied(update)


async def test_unknown_subcommand_and_bad_admin_id() -> None:
    redis = FakeRedis()
    await _make_admin(redis, 42)
    update = _update(42)
    await cmd_setup(update, _ctx(redis, ["nonsense"]))
    assert "unknown subcommand" in _replied(update)

    update = _update(42)
    await cmd_setup(update, _ctx(redis, ["admins", "add", "not-a-number"]))
    assert "not a user id" in _replied(update)
