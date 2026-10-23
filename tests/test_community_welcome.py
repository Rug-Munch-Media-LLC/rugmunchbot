"""Welcome/goodbye: opt-in, plain text, never blocks and never raises."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import rugmunchbot.config as botcfg
from rugmunchbot.moderation import config, store, welcome
from rugmunchbot.moderation.harness_fakes import (
    CHAT_ID,
    FakeChat,
    FakeMessage,
    FakeUpdate,
    FakeUser,
)

OWNER_ID = 424242


class RecordingBot:
    """Records outbound calls; optionally fails like a rights-less bot."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail

    async def send_message(self, **kwargs):
        if self.fail:
            raise RuntimeError("bot is not an admin")
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


def _enable(path, **community):
    path.write_text(json.dumps({"chats": {str(CHAT_ID): {"community": community}}}))


def test_render_substitutes_and_truncates():
    assert welcome.render("Hi {name} in {chat}", "Ada", "RugMunch") == "Hi Ada in RugMunch"
    assert welcome.render("Hi {name}", "", "") == "Hi friend"
    assert len(welcome.render("y" * 900, "Ada", "")) == 600
    assert "{other}" in welcome.render("{other}", "Ada", "")


async def test_join_is_silent_by_default():
    bot = RecordingBot()
    update = FakeUpdate(
        message=FakeMessage(new_chat_members=[FakeUser(id=5)]),
        chat=FakeChat(id=CHAT_ID),
        user=FakeUser(id=5),
    )
    assert await welcome.handle_join(update, SimpleNamespace(bot=bot)) is False
    assert bot.calls == []


async def test_join_sends_plain_text_when_enabled(cfg_path):
    _enable(cfg_path, welcome_enabled=True, welcome_text="Welcome {name}!")
    bot = RecordingBot()
    update = FakeUpdate(
        message=FakeMessage(new_chat_members=[FakeUser(id=5, full_name="Ada")]),
        chat=FakeChat(id=CHAT_ID),
        user=FakeUser(id=5),
    )
    assert await welcome.handle_join(update, SimpleNamespace(bot=bot)) is True
    name, kwargs = bot.calls[0]
    assert name == "send_message"
    assert kwargs["text"] == "Welcome Ada!"
    assert "parse_mode" not in kwargs  # admin text is never parsed as HTML


async def test_enabled_flag_without_text_sends_nothing(cfg_path):
    _enable(cfg_path, welcome_enabled=True, welcome_text="   ")
    bot = RecordingBot()
    update = FakeUpdate(
        message=FakeMessage(new_chat_members=[FakeUser(id=5)]),
        chat=FakeChat(id=CHAT_ID),
        user=FakeUser(id=5),
    )
    assert await welcome.handle_join(update, SimpleNamespace(bot=bot)) is False
    assert bot.calls == []


async def test_bot_members_are_ignored(cfg_path):
    _enable(cfg_path, welcome_enabled=True, welcome_text="Welcome {name}!")
    bot = RecordingBot()
    update = FakeUpdate(
        message=FakeMessage(new_chat_members=[FakeUser(id=9, is_bot=True)]),
        chat=FakeChat(id=CHAT_ID),
        user=FakeUser(id=9),
    )
    assert await welcome.handle_join(update, SimpleNamespace(bot=bot)) is False


async def test_goodbye_when_enabled(cfg_path):
    _enable(cfg_path, goodbye_enabled=True, goodbye_text="Bye {name}")
    bot = RecordingBot()
    update = FakeUpdate(
        message=FakeMessage(left_chat_member=FakeUser(id=5, full_name="Ada")),
        chat=FakeChat(id=CHAT_ID),
        user=FakeUser(id=5),
    )
    assert await welcome.handle_leave(update, SimpleNamespace(bot=bot)) is True
    assert bot.calls[0][1]["text"] == "Bye Ada"


async def test_send_failure_is_swallowed(cfg_path):
    _enable(cfg_path, welcome_enabled=True, welcome_text="Welcome {name}!")
    bot = RecordingBot(fail=True)
    update = FakeUpdate(
        message=FakeMessage(new_chat_members=[FakeUser(id=5)]),
        chat=FakeChat(id=CHAT_ID),
        user=FakeUser(id=5),
    )
    assert await welcome.handle_join(update, SimpleNamespace(bot=bot)) is False
