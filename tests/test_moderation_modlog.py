"""Modlog delivery — the Heimdall-parity "admin alerts" path (offline).

No network: the bot is a recorder, config is a temp JSON file and the audit
trail goes to ``tmp_path``. What is asserted here is exactly the safety
contract: off by default, never raises, escapes user text.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rugmunchbot.moderation import actions, audit, chatconf, config, modlog, store
from rugmunchbot.moderation.models import Action, MsgContext

CHAT = -1001234567890
LOG = -1009876543210


class FakeBot:
    def __init__(self, *, fail: bool = False, linked: int | None = None) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail = fail
        self.linked = linked

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("chat not found")
        self.sent.append((chat_id, text))

    async def get_chat(self, chat_id: int):
        return SimpleNamespace(linked_chat_id=self.linked)


def entry() -> dict:
    return audit.build_entry(
        action_id="20261024T000000-abcdef",
        chat_id=CHAT,
        chat_title="Rug Munch",
        user_id=4242,
        username="<b>evil</b>",
        rule="scam_link",
        action="ban",
        reason="claim your airdrop",
        matched="drainer-claim.xyz",
        dry_run=False,
        applied=True,
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    store.use_backend(None)
    store.reset_memory()
    audit.use_audit_path(tmp_path / "audit.jsonl")
    cfg_file = tmp_path / "moderation.json"
    cfg_file.write_text(json.dumps({"chats": {}}), encoding="utf-8")
    config.use_config_path(cfg_file)
    yield
    store.reset_memory()


def cfg(**community):
    return chatconf.CommunityConfig.from_dict(CHAT, {"community": community})


async def test_disabled_by_default_sends_nothing():
    bot = FakeBot()
    assert await modlog.deliver(bot, entry(), chat_id=CHAT, cfg=cfg(), title="x") is False
    assert bot.sent == []


async def test_explicit_chat_receives_escaped_line():
    bot = FakeBot()
    ok = await modlog.deliver(
        bot, entry(), chat_id=CHAT, cfg=cfg(modlog_enabled=True, modlog_chat_id=LOG), title="Rug"
    )
    assert ok is True
    dest, text = bot.sent[0]
    assert dest == LOG
    assert "BAN" in text
    assert "APPLIED" in text
    assert "&lt;b&gt;evil&lt;/b&gt;" in text  # user text is escaped, never raw HTML
    assert "<b>evil</b>" not in text


async def test_linked_channel_fallback_and_opt_in():
    bot = FakeBot(linked=LOG)
    base = cfg(modlog_enabled=True)
    assert await modlog.deliver(bot, entry(), chat_id=CHAT, cfg=base, title="") is False
    assert bot.sent == []
    ok = await modlog.deliver(
        bot, entry(), chat_id=CHAT, cfg=cfg(modlog_enabled=True, modlog_linked=True), title=""
    )
    assert ok is True
    assert bot.sent[0][0] == LOG


async def test_telegram_error_is_swallowed():
    bot = FakeBot(fail=True)
    ok = await modlog.deliver(
        bot, entry(), chat_id=CHAT, cfg=cfg(modlog_enabled=True, modlog_chat_id=LOG), title=""
    )
    assert ok is False  # never raises into the dispatcher


async def test_alert_action_is_mirrored_even_in_dry_run(tmp_path):
    cfg_file = tmp_path / "live.json"
    cfg_file.write_text(
        json.dumps(
            {"chats": {str(CHAT): {"community": {"modlog_enabled": True, "modlog_chat_id": LOG}}}}
        ),
        encoding="utf-8",
    )
    config.use_config_path(cfg_file)
    bot = FakeBot()
    policy = SimpleNamespace(dry_run=True, mute_minutes=10, ban_minutes=0)
    ctx = MsgContext(
        chat_id=CHAT, chat_title="Rug", user_id=4242, username="u", display_name="U", text="hi"
    )
    record = await actions.execute(
        bot, ctx, policy, Action(rule="chat_raid", action="alert", reason="20 msgs/10s")
    )
    assert record["dry_run"] is True
    assert bot.sent
    assert "ALERT" in bot.sent[0][1]
    rows = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert rows[-1]["rule"] == "chat_raid"
