"""Notes and filters: admin-gated writes, per-chat scope, reply cap."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import rugmunchbot.config as botcfg
from rugmunchbot.moderation import chatconf, config, filters, notes, store
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    FakeChat,
    FakeMessage,
    FakeUpdate,
    FakeUser,
    ctx,
)

OWNER_ID = 424242
ADMIN_ID = 7
MEMBER_ID = 8


class Rig:
    def __init__(self, status: str = "administrator") -> None:
        self.calls: list[tuple[str, dict]] = []
        self.status = status

    async def get_chat_member(self, **kwargs):
        return SimpleNamespace(status=self.status)

    async def send_message(self, **kwargs):
        self.calls.append(("send_message", kwargs))


@pytest.fixture
def cfg_path(_rig, tmp_path):
    return tmp_path / "moderation.json"


@pytest.fixture(autouse=True)
def _rig(tmp_path, monkeypatch):
    monkeypatch.setattr(botcfg, "OWNER_IDS", {OWNER_ID}, raising=False)
    store.use_backend(store.MemoryStore())
    store.reset_memory()
    filters._hits.clear()
    path = tmp_path / "moderation.json"
    path.write_text(json.dumps({"chats": {str(CHAT_ID): {}}}))
    original = config.CONFIG_PATH
    config.use_config_path(path)
    yield
    config.use_config_path(original)
    store.use_backend(None)


def _save_update(text: str, user_id: int = ADMIN_ID) -> tuple[FakeUpdate, FakeMessage]:
    message = FakeMessage(text=text)
    update = FakeUpdate(message=message, chat=FakeChat(id=CHAT_ID), user=FakeUser(id=user_id))
    return update, message


def test_note_name_is_sanitised():
    assert notes.note_name(" Rules ") == "rules"
    assert notes.note_name("#abc-1") == "abc-1"
    assert notes.note_name("bad name") is None
    assert notes.note_name("") is None
    assert notes.note_name("x" * 40) is None


async def test_admin_saves_and_everyone_reads():
    update, message = _save_update("/save rules Be excellent")
    await notes.cmd_save(update, SimpleNamespace(bot=Rig(), args=["rules", "Be", "excellent"]))
    assert "Saved" in message.replies[-1]

    read, read_msg = _save_update("/get rules", user_id=MEMBER_ID)
    await notes.cmd_get(read, SimpleNamespace(bot=Rig(), args=["rules"]))
    assert read_msg.replies[-1] == "Be excellent"


async def test_non_admin_cannot_save_by_default():
    update, message = _save_update("/save rules hi", user_id=MEMBER_ID)
    await notes.cmd_save(update, SimpleNamespace(bot=Rig("member"), args=["rules", "hi"]))
    assert "Chat admins only" in message.replies[-1]
    assert await store.all_fields(store.chat_key(CHAT_ID, "notes")) == {}


async def test_non_admin_can_save_when_config_allows(tmp_path):
    config.use_config_path(tmp_path / "moderation.json")
    (tmp_path / "moderation.json").write_text(
        json.dumps({"chats": {str(CHAT_ID): {"community": {"notes_admins_only": False}}}})
    )
    update, message = _save_update("/save faq read pins", user_id=MEMBER_ID)
    await notes.cmd_save(update, SimpleNamespace(bot=Rig("member"), args=["faq", "read pins"]))
    assert "Saved" in message.replies[-1]


async def test_save_from_reply_uses_replied_text():
    message = FakeMessage(text="/save quoted")
    message.reply_to_message = FakeMessage(text="the pinned words")
    update = FakeUpdate(message=message, chat=FakeChat(id=CHAT_ID), user=FakeUser(id=ADMIN_ID))
    await notes.cmd_save(update, SimpleNamespace(bot=Rig(), args=["quoted"]))
    key = store.chat_key(CHAT_ID, "notes")
    assert await store.get_field(key, "quoted") == "the pinned words"


async def test_notes_list_and_clear():
    key = store.chat_key(CHAT_ID, "notes")
    await store.set_field(key, "a", "one")
    await store.set_field(key, "b", "two")

    update, message = _save_update("/notes")
    await notes.cmd_notes(update, SimpleNamespace(bot=Rig()))
    assert "#a" in message.replies[-1]
    assert "#b" in message.replies[-1]

    clear, clear_msg = _save_update("/clear a")
    await notes.cmd_clear(clear, SimpleNamespace(bot=Rig(), args=["a"]))
    assert "Deleted" in clear_msg.replies[-1]
    assert await store.get_field(key, "a") is None


async def test_clear_requires_admin():
    key = store.chat_key(CHAT_ID, "notes")
    await store.set_field(key, "a", "one")
    update, message = _save_update("/clear a", user_id=MEMBER_ID)
    await notes.cmd_clear(update, SimpleNamespace(bot=Rig("member"), args=["a"]))
    assert "Chat admins only" in message.replies[-1]
    assert await store.get_field(key, "a") == "one"


def test_filter_normalise_and_match():
    assert filters.normalise("  Rug   Pull ") == "rug pull"
    assert filters.normalise("x") is None
    table = {"rug pull": "report it", "rug": "shorter"}
    assert filters.match("this is a RUG PULL", table) == "report it"
    assert filters.match("nothing here", table) is None


async def test_filter_reply_is_off_by_default(tmp_path):
    bot = Rig()
    update, _ = _save_update("rug pull incoming")
    assert (
        await filters.maybe_reply(
            update, ctx("rug pull incoming"), chatconf.load_community_config(CHAT_ID)
        )
        is False
    )
    assert bot.calls == []


async def test_filter_reply_once_then_throttled(tmp_path):
    (tmp_path / "moderation.json").write_text(
        json.dumps(
            {
                "chats": {
                    str(CHAT_ID): {
                        "community": {"filters_enabled": True, "filter_replies_per_min": 1}
                    }
                }
            }
        )
    )
    config.use_config_path(tmp_path / "moderation.json")
    await store.set_field(store.chat_key(CHAT_ID, "filters"), "rug pull", "report it")
    cfg = chatconf.load_community_config(CHAT_ID)

    first, first_msg = _save_update("rug pull incoming")
    assert await filters.maybe_reply(first, ctx("rug pull incoming"), cfg) is True
    assert first_msg.replies == ["report it"]

    second, second_msg = _save_update("another rug pull")
    assert await filters.maybe_reply(second, ctx("another rug pull"), cfg) is False
    assert second_msg.replies == []


async def test_filter_command_requires_admin():
    update, message = _save_update("/filter rug report it", user_id=MEMBER_ID)
    await filters.cmd_filter(
        update, SimpleNamespace(bot=Rig("member"), args=["rug", "report", "it"])
    )
    assert "Chat admins only" in message.replies[-1]
    assert await store.all_fields(store.chat_key(CHAT_ID, "filters")) == {}

    admin, admin_msg = _save_update("/filter rug report it")
    await filters.cmd_filter(admin, SimpleNamespace(bot=Rig(), args=["rug", "report", "it"]))
    assert "saved" in admin_msg.replies[-1]
    assert await store.get_field(store.chat_key(CHAT_ID, "filters"), "rug") == "report it"
