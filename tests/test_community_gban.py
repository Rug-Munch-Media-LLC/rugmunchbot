"""Global ban list: owner-only writes, enforcement needs two explicit opt-ins."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import rugmunchbot.config as botcfg
from rugmunchbot.moderation import audit, chatconf, config, gban, store
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    FakeChat,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    ctx,
    policy,
)

OWNER_ID = 424242
ADMIN_ID = 7
MEMBER_ID = 8
TARGET = 666


class Rig:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def get_chat_member(self, **kwargs):
        return SimpleNamespace(status="administrator")

    async def ban_chat_member(self, **kwargs):
        self.calls.append(("ban_chat_member", kwargs))

    async def delete_message(self, **kwargs):
        self.calls.append(("delete_message", kwargs))


@pytest.fixture
def cfg_path(_rig, tmp_path):
    return tmp_path / "moderation.json"


@pytest.fixture(autouse=True)
def _rig(tmp_path, monkeypatch):
    monkeypatch.setattr(botcfg, "OWNER_IDS", {OWNER_ID}, raising=False)
    store.use_backend(store.MemoryStore())
    store.reset_memory()
    audit.use_audit_path(tmp_path / "actions.jsonl")
    path = tmp_path / "moderation.json"
    path.write_text(json.dumps({"chats": {str(CHAT_ID): {"dry_run": False}}}))
    original = config.CONFIG_PATH
    config.use_config_path(path)
    yield
    config.use_config_path(original)
    store.use_backend(None)


def _cmd(text: str, user_id: int, reply_from: int | None = None) -> tuple:
    message = FakeMessage(text=text)
    if reply_from is not None:
        message.reply_to_message = FakeMessage(text="spam", from_user=FakeUser(id=reply_from))
    update = FakeUpdate(message=message, chat=FakeChat(id=CHAT_ID), user=FakeUser(id=user_id))
    return update, message


def _cfg(path, **community):
    path.write_text(
        json.dumps({"chats": {str(CHAT_ID): {"dry_run": False, "community": community}}})
    )
    config.use_config_path(path)


async def test_owner_adds_and_lists():
    update, message = _cmd("/gban 666 spamming", OWNER_ID)
    await gban.cmd_gban(update, SimpleNamespace(bot=Rig(), args=["666", "spamming"]))
    assert "globally banned" in message.replies[-1]
    assert await store.get_field(store.global_key("gbans"), "666") is not None

    listing, list_msg = _cmd("/gbans", OWNER_ID)
    await gban.cmd_gbans(listing, SimpleNamespace(bot=Rig(), args=[]))
    assert "666" in list_msg.replies[-1]
    assert "spamming" in list_msg.replies[-1]


async def test_non_owner_cannot_gban_or_ungban():
    update, message = _cmd("/gban 666", ADMIN_ID)
    await gban.cmd_gban(update, SimpleNamespace(bot=Rig(), args=["666"]))
    assert "Bot owners only" in message.replies[-1]
    assert await store.all_fields(store.global_key("gbans")) == {}


async def test_target_from_reply():
    update, message = _cmd("/gban", OWNER_ID, reply_from=TARGET)
    await gban.cmd_gban(update, SimpleNamespace(bot=Rig(), args=[]))
    assert "globally banned" in message.replies[-1]
    assert await store.get_field(store.global_key("gbans"), str(TARGET)) is not None


async def test_ungban_round_trip():
    await store.set_field(store.global_key("gbans"), "666", '{"reason": "x"}')
    update, message = _cmd("/ungban 666", OWNER_ID)
    await gban.cmd_ungban(update, SimpleNamespace(bot=Rig(), args=["666"]))
    assert "removed from the global ban list" in message.replies[-1]
    assert await store.all_fields(store.global_key("gbans")) == {}


async def test_enforcement_is_off_without_explicit_config():
    await store.set_field(store.global_key("gbans"), str(MEMBER_ID), '{"reason": "x"}')
    cfg = chatconf.load_community_config(CHAT_ID)
    bot = Rig()
    assert await gban.enforce(bot, ctx("hello", user_id=MEMBER_ID), policy(), cfg) is False
    assert bot.calls == []


async def test_enforcement_never_bans_without_opt_in_even_in_live_chat(cfg_path):
    _cfg(cfg_path, enforce_gban=False)
    await store.set_field(store.global_key("gbans"), str(MEMBER_ID), '{"reason": "x"}')
    bot = Rig()
    cfg = chatconf.load_community_config(CHAT_ID)
    assert (
        await gban.enforce(bot, ctx("hello", user_id=MEMBER_ID), policy(dry_run=False), cfg)
        is False
    )
    assert bot.calls == []


async def test_opt_in_but_dry_run_only_audits(cfg_path):
    _cfg(cfg_path, enforce_gban=True)
    await store.set_field(store.global_key("gbans"), str(MEMBER_ID), '{"reason": "x"}')
    bot = Rig()
    cfg = chatconf.load_community_config(CHAT_ID)
    assert (
        await gban.enforce(bot, ctx("hello", user_id=MEMBER_ID), policy(dry_run=True), cfg) is False
    )
    assert bot.calls == []
    row = audit.tail(CHAT_ID, 1)[0]
    assert row["rule"] == "gban"
    assert row["dry_run"] is True
    assert row["applied"] is False


async def test_opt_in_live_chat_bans_and_deletes(cfg_path):
    _cfg(cfg_path, enforce_gban=True)
    await store.set_field(store.global_key("gbans"), str(MEMBER_ID), '{"reason": "x"}')
    bot = Rig()
    cfg = chatconf.load_community_config(CHAT_ID)
    consumed = await gban.enforce(
        bot, ctx("hello", user_id=MEMBER_ID, message_id=5), policy(dry_run=False), cfg
    )
    assert consumed is True
    names = [name for name, _ in bot.calls]
    assert names == ["ban_chat_member", "delete_message"]
    assert audit.tail(CHAT_ID, 1)[0]["applied"] is True


async def test_chat_admin_is_never_auto_banned(cfg_path):
    _cfg(cfg_path, enforce_gban=True)
    await store.set_field(store.global_key("gbans"), str(ADMIN_ID), '{"reason": "x"}')
    bot = Rig()
    cfg = chatconf.load_community_config(CHAT_ID)
    verdict = await gban.enforce(
        bot, ctx("hi", user_id=ADMIN_ID, is_admin=True), policy(dry_run=False), cfg
    )
    assert verdict is False
    assert bot.calls == []


async def test_non_listed_user_passes(cfg_path):
    _cfg(cfg_path, enforce_gban=True)
    bot = Rig()
    cfg = chatconf.load_community_config(CHAT_ID)
    assert (
        await gban.enforce(bot, ctx("hi", user_id=MEMBER_ID), policy(dry_run=False), cfg) is False
    )
    assert bot.calls == []
