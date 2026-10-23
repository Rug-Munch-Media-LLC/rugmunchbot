"""Handler wiring tests for /modstatus … /modpardon — offline, no network."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from rugmunchbot.community.moderation import ModerationEngine, Policy
from rugmunchbot.community.moderation_config import policy_key
from rugmunchbot.community.store import COMMUNITY_CLIENT_KEY
from rugmunchbot.handlers import moderation as mod


class FakeBot:
    def __init__(self, admins: tuple[int, ...] = ()) -> None:
        self.calls: list[tuple] = []
        self._admins = [SimpleNamespace(user=SimpleNamespace(id=i)) for i in admins]

    async def get_chat_administrators(self, chat_id: int):
        return self._admins

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.calls.append(("delete", chat_id, message_id))

    async def restrict_chat_member(self, chat_id: int, user_id: int, **kwargs) -> None:
        self.calls.append(("mute", chat_id, user_id, kwargs.get("until_date")))

    async def ban_chat_member(self, chat_id: int, user_id: int) -> None:
        self.calls.append(("ban", chat_id, user_id))

    async def send_message(self, chat_id: int, text: str) -> None:
        self.calls.append(("say", chat_id, text))


class FakeMessage:
    def __init__(self, message_id: int = 5, reply_to=None) -> None:
        self.message_id = message_id
        self.reply_to_message = reply_to
        self.replies: list[str] = []

    async def reply_text(self, text: str):
        self.replies.append(text)
        return SimpleNamespace(message_id=99)


def _update(user_id: int = 1, message: FakeMessage | None = None) -> SimpleNamespace:
    msg = message or FakeMessage()
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=-100),
        effective_message=msg,
        message=msg,
    )


class FakeRedis:
    """Async Redis stand-in holding at most one stored policy document."""

    def __init__(self, document: dict | None = None) -> None:
        self.values: dict[str, str] = {}
        if document is not None:
            self.values[policy_key(-100)] = json.dumps(document)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True


def _context(
    engine: ModerationEngine | None = None,
    bot: FakeBot | None = None,
    args=(),
    redis: FakeRedis | None = None,
):
    bot = bot or FakeBot()
    data = {} if engine is None else {mod.ENGINE_KEY: engine}
    data[COMMUNITY_CLIENT_KEY] = redis or FakeRedis()
    return SimpleNamespace(bot=bot, application=SimpleNamespace(bot_data=data), args=list(args))


def _handlers() -> dict[str, object]:
    found: list[object] = []
    app = SimpleNamespace(add_handler=lambda handler, group=0: found.append(handler))
    mod.register_moderation_commands(app)
    return {next(iter(h.commands)): h.callback for h in found}


def _call(name: str, update, context) -> None:
    asyncio.run(_handlers()[name](update, context))


def test_register_adds_nine_commands() -> None:
    assert set(_handlers()) == {
        "modstatus",
        "modrules",
        "modactions",
        "warn",
        "mute",
        "ban",
        "trust",
        "untrust",
        "modpardon",
    }


def test_modrules_is_public_and_mentions_escalation() -> None:
    update, context = _update(), _context()
    _call("modrules", update, context)
    assert "mute at 3" in update.effective_message.replies[0]


def test_modstatus_reports_dry_run() -> None:
    update = _update()
    _call("modstatus", update, _context())
    assert "DRY-RUN" in update.effective_message.replies[0]


def test_admin_command_denied_for_outsider() -> None:
    update, context = _update(user_id=42), _context()
    _call("warn", update, context)
    assert update.effective_message.replies == [mod.DENIED]


def test_telegram_admin_allowed() -> None:
    update = _update(user_id=42)
    context = _context(bot=FakeBot(admins=(42,)), args=["7", "spam"])
    _call("warn", update, context)
    assert "user 7" in update.effective_message.replies[0]


def test_engine_admin_allowed() -> None:
    engine = ModerationEngine(admins=[42])
    update = _update(user_id=42)
    _call("warn", update, _context(engine=engine, args=["7"]))
    assert "user 7" in update.effective_message.replies[0]


def test_trust_then_untrust() -> None:
    engine = ModerationEngine(admins=[42])
    _call("trust", _update(user_id=42), _context(engine=engine, args=["7"]))
    assert engine.is_trusted(7)
    _call("untrust", _update(user_id=42), _context(engine=engine, args=["7"]))
    assert not engine.is_trusted(7)


def test_target_from_reply() -> None:
    engine = ModerationEngine(admins=[42])
    reply = FakeMessage(message_id=3)
    reply.from_user = SimpleNamespace(id=555)
    update = _update(user_id=42, message=FakeMessage(reply_to=reply))
    _call("mute", update, _context(engine=engine))
    assert engine.actions(-100)[0]["user_id"] == 555


def test_target_missing_shows_usage() -> None:
    engine = ModerationEngine(admins=[42])
    update = _update(user_id=42)
    _call("ban", update, _context(engine=engine))
    assert "Usage: /ban" in update.effective_message.replies[0]


def test_live_mute_reaches_telegram_client() -> None:
    engine = ModerationEngine(admins=[42])
    bot = FakeBot()
    redis = FakeRedis({"dry_run": False})
    _call("mute", _update(user_id=42), _context(engine=engine, bot=bot, args=["7"], redis=redis))
    assert any(call[0] == "mute" and call[2] == 7 for call in bot.calls)


def test_dry_run_never_calls_telegram() -> None:
    engine = ModerationEngine(admins=[42])
    bot = FakeBot()
    _call("ban", _update(user_id=42), _context(engine=engine, bot=bot, args=["7"]))
    assert bot.calls == []


def test_modactions_lists_audit() -> None:
    engine = ModerationEngine(admins=[42])
    asyncio.run(engine.enforce_manual("trust", -100, 7))
    update = _update(user_id=42)
    _call("modactions", update, _context(engine=engine))
    assert "trust user 7" in update.effective_message.replies[0]


def test_modactions_empty() -> None:
    update = _update(user_id=42)
    _call("modactions", update, _context(engine=ModerationEngine(admins=[42])))
    assert "No moderation actions" in update.effective_message.replies[0]
