"""Scheduled messages: UTC HH:MM parsing, opt-in, one post per minute."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import rugmunchbot.config as botcfg
from rugmunchbot.moderation import chatconf, config, schedule, store
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    FakeChat,
    FakeMessage,
    FakeUpdate,
    FakeUser,
)

ADMIN_ID = 7
MEMBER_ID = 8
OWNER_ID = 424242
DUE = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)


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
    path = tmp_path / "moderation.json"
    path.write_text(json.dumps({"chats": {str(CHAT_ID): {}}}))
    original = config.CONFIG_PATH
    config.use_config_path(path)
    yield
    config.use_config_path(original)
    store.use_backend(None)


def _update(text: str, user_id: int = ADMIN_ID) -> tuple:
    message = FakeMessage(text=text)
    return (
        FakeUpdate(message=message, chat=FakeChat(id=CHAT_ID), user=FakeUser(id=user_id)),
        message,
    )


def _enable(path, **community):
    path.write_text(json.dumps({"chats": {str(CHAT_ID): {"community": community}}}))
    config.use_config_path(path)


def test_parse_time():
    assert schedule.parse_time("9:05") == "09:05"
    assert schedule.parse_time("23:59") == "23:59"
    assert schedule.parse_time("24:00") is None
    assert schedule.parse_time("12:60") is None
    assert schedule.parse_time("noon") is None
    assert schedule.parse_time("") is None


async def test_schedule_refused_when_disabled():
    update, message = _update("/schedule 09:30 gm")
    await schedule.cmd_schedule(update, SimpleNamespace(bot=Rig(), args=["09:30", "gm"]))
    assert "disabled for this chat" in message.replies[-1]
    assert await store.all_fields(store.chat_key(CHAT_ID, "sched")) == {}


async def test_schedule_requires_admin(cfg_path):
    _enable(cfg_path, scheduled_enabled=True)
    update, message = _update("/schedule 09:30 gm", user_id=MEMBER_ID)
    await schedule.cmd_schedule(update, SimpleNamespace(bot=Rig("member"), args=["09:30", "gm"]))
    assert "Chat admins only" in message.replies[-1]


async def test_schedule_add_list_remove(cfg_path):
    _enable(cfg_path, scheduled_enabled=True)
    update, message = _update("/schedule 09:30 gm frens")
    await schedule.cmd_schedule(update, SimpleNamespace(bot=Rig(), args=["09:30", "gm frens"]))
    assert "Scheduled" in message.replies[-1]
    entries = await store.all_fields(store.chat_key(CHAT_ID, "sched"))
    assert len(entries) == 1
    entry_id = next(iter(entries))

    listing, list_msg = _update("/schedules")
    await schedule.cmd_schedules(listing, SimpleNamespace(bot=Rig(), args=[]))
    assert "09:30" in list_msg.replies[-1]
    assert "gm frens" in list_msg.replies[-1]

    remove_update, remove_msg = _update(f"/unschedule {entry_id}")
    await schedule.cmd_unschedule(remove_update, SimpleNamespace(bot=Rig(), args=[entry_id]))
    assert "Removed" in remove_msg.replies[-1]
    assert await store.all_fields(store.chat_key(CHAT_ID, "sched")) == {}


async def test_tick_posts_once_per_minute(cfg_path):
    _enable(cfg_path, scheduled_enabled=True)
    await store.set_field(
        store.chat_key(CHAT_ID, "sched"),
        "abc123",
        json.dumps({"t": "09:30", "text": "gm frens", "last": ""}),
    )
    bot = Rig()
    assert await schedule.tick(SimpleNamespace(bot=bot), now=DUE) == 1
    assert bot.calls[0][1]["text"] == "gm frens"
    # Same minute again (restart or overlapping tick) must not double-post.
    assert await schedule.tick(SimpleNamespace(bot=bot), now=DUE) == 0
    assert len(bot.calls) == 1


async def test_tick_ignores_other_minutes_and_disabled_chats(cfg_path):
    _enable(cfg_path, scheduled_enabled=True)
    await store.set_field(
        store.chat_key(CHAT_ID, "sched"), "abc123", json.dumps({"t": "10:00", "text": "x"})
    )
    bot = Rig()
    assert await schedule.tick(SimpleNamespace(bot=bot), now=DUE) == 0

    _enable(cfg_path, scheduled_enabled=False)
    await store.set_field(
        store.chat_key(CHAT_ID, "sched"), "abc123", json.dumps({"t": "09:30", "text": "x"})
    )
    assert await schedule.tick(SimpleNamespace(bot=bot), now=DUE) == 0
    assert bot.calls == []


async def test_tick_survives_a_failing_send(cfg_path):
    _enable(cfg_path, scheduled_enabled=True)
    await store.set_field(
        store.chat_key(CHAT_ID, "sched"), "abc123", json.dumps({"t": "09:30", "text": "x"})
    )

    class Broken:
        async def send_message(self, **kwargs):
            raise RuntimeError("chat migrated")

    assert await schedule.tick(SimpleNamespace(bot=Broken()), now=DUE) == 0


def test_disabled_chat_has_no_effective_settings():
    cfg = chatconf.load_community_config(CHAT_ID)
    assert cfg.scheduled_enabled is False
    assert cfg.schedule_max == 10
