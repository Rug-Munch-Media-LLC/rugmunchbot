"""Flood control tests — anti-abuse protection."""

from datetime import UTC, datetime

import pytest
from telegram import Chat, Message, Update, User
from telegram.ext import CommandHandler

from rugmunchbot.flood_control import FloodController


@pytest.fixture
def controller():
    fc = FloodController.__new__(FloodController)
    fc._redis = None  # no Redis in tests
    return fc


def test_flood_controller_exists(controller):
    assert controller is not None


def test_get_flood_controller_singleton():
    from rugmunchbot.flood_control import get_flood_controller

    fc1 = get_flood_controller()
    fc2 = get_flood_controller()
    assert fc1 is fc2


def test_command_reg_flood_gate_blocks_before_handler(monkeypatch):
    """``bot._commands._reg`` throttles a flooded command before the handler runs.

    The gate reuses the same FloodController limits (3/s, 30/min); a rejected
    command replies "Slow down" and returns WITHOUT calling the handler or
    recording activity.
    """
    import rugmunchbot.bot._commands as cmds

    async def _fake_check_flood(user_id):
        return (False, 7, None)

    # Patch the controller the wrapper uses, and force the env gate on.
    monkeypatch.setattr(cmds, "_check_flood", _fake_check_flood)
    monkeypatch.setattr(cmds, "_COMMAND_FLOOD_ENABLED", True)

    handler_called = {"n": 0}

    async def fake_handler(update, ctx):
        handler_called["n"] += 1

    class _FakeApp:
        def __init__(self):
            self.handlers: list = []

        def add_handler(self, h):
            self.handlers.append(h)

    app = _FakeApp()
    cmds._reg(app, "scan", fake_handler)
    ch = next(h for h in app.handlers if isinstance(h, CommandHandler) and "scan" in h.commands)

    replies: list[str] = []

    async def fake_reply_text(self, text, **kwargs):
        replies.append(text)

    user = User(id=123, first_name="T", is_bot=False, username="tester")
    chat = Chat(id=456, type=Chat.PRIVATE)
    msg = Message(
        message_id=1,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="/scan",
    )
    # PTB TelegramObject is frozen — patch the class method, not the instance.
    monkeypatch.setattr(Message, "reply_text", fake_reply_text)
    update = Update(update_id=1, message=msg)

    async def run():
        await ch.callback(update, None)

    import asyncio

    asyncio.run(run())

    assert handler_called["n"] == 0  # handler never ran
    assert replies, "expected a 'Slow down' reply"
    assert "Slow down" in replies[0]
    assert "7s" in replies[0]


def test_command_reg_flood_gate_allows_when_under_limit(monkeypatch):
    """An allowed command passes through to the handler + records activity."""
    import rugmunchbot.bot._commands as cmds

    async def _fake_check_flood(user_id):
        return (True, 0, "rcpt")

    monkeypatch.setattr(cmds, "_check_flood", _fake_check_flood)
    monkeypatch.setattr(cmds, "_COMMAND_FLOOD_ENABLED", True)
    # Activity tracking writes to the DB / Redis — stub it so the test is
    # hermetic (the wrapper must still call the handler).
    record_calls: list[str] = []
    monkeypatch.setattr(cmds.db, "record_activity", lambda **kw: record_calls.append("activity"))
    monkeypatch.setattr(cmds, "track_command", lambda name: record_calls.append(f"tracked:{name}"))

    called = {"n": 0}

    async def fake_handler(update, ctx):
        called["n"] += 1

    class _FakeApp:
        def __init__(self):
            self.handlers: list = []

        def add_handler(self, h):
            self.handlers.append(h)

    app = _FakeApp()
    cmds._reg(app, "price", fake_handler)
    ch = next(h for h in app.handlers if isinstance(h, CommandHandler) and "price" in h.commands)

    user = User(id=7, first_name="A", is_bot=False)
    chat = Chat(id=9, type=Chat.PRIVATE)
    msg = Message(
        message_id=2,
        date=datetime.now(UTC),
        chat=chat,
        from_user=user,
        text="/price",
    )
    update = Update(update_id=2, message=msg)
    import asyncio

    asyncio.run(ch.callback(update, None))
    assert called["n"] == 1
    assert record_calls == ["activity", "tracked:price"]
