"""Group ``/report`` — flag a message for the admins (offline).

The report is evidence, never a verdict: these tests pin that nothing is
deleted, muted or banned by a report, that the throttle holds, and that an
unconfigured chat tells the member the truth instead of pretending.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rugmunchbot.moderation import audit, config, report, store

CHAT = -1001234567890
USER = 777
TARGET = 4242


class FakeBot:
    def __init__(self, *, fail: bool = False) -> None:
        self.sent: list[tuple[int, str]] = []
        self.fail = fail

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if self.fail:
            raise RuntimeError("nope")
        self.sent.append((chat_id, text))


class FakeMessage:
    def __init__(self, *, reply_to=None, text: str = "/report spam", message_id: int = 90) -> None:
        self.message_id = message_id
        self.reply_to_message = reply_to
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


def target_message():
    return SimpleNamespace(
        message_id=41,
        text="free sol at drainer.example",
        caption=None,
        from_user=SimpleNamespace(id=TARGET, username="scammer"),
    )


def make_update(message, *, chat_id: int = CHAT, user_id: int = USER):
    return SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=chat_id, title="Rug Munch", type="supergroup"),
        effective_user=SimpleNamespace(id=user_id, username="member"),
    )


def context(bot: FakeBot):
    return SimpleNamespace(bot=bot)


@pytest.fixture(autouse=True)
def _isolated(tmp_path):
    store.use_backend(None)
    store.reset_memory()
    audit.use_audit_path(tmp_path / "audit.jsonl")
    report._seen.clear()
    cfg_file = tmp_path / "moderation.json"
    cfg_file.write_text(json.dumps({"chats": {}}), encoding="utf-8")
    config.use_config_path(cfg_file)
    yield
    report._seen.clear()
    store.reset_memory()


def enable_modlog(tmp_path, log_id: int = -1009876543210) -> None:
    cfg_file = tmp_path / "moderation.json"
    cfg_file.write_text(
        json.dumps(
            {
                "chats": {
                    str(CHAT): {"community": {"modlog_enabled": True, "modlog_chat_id": log_id}}
                }
            }
        ),
        encoding="utf-8",
    )
    config.use_config_path(cfg_file)


async def test_requires_a_reply():
    message = FakeMessage(reply_to=None)
    bot = FakeBot()
    await report.handle_group_report(make_update(message), context(bot))
    assert "Reply to the message" in message.replies[0]
    assert bot.sent == []


async def test_reports_to_modlog_and_audits(tmp_path):
    enable_modlog(tmp_path)
    message = FakeMessage(reply_to=target_message(), text="/report drainer link")
    bot = FakeBot()
    await report.handle_group_report(make_update(message), context(bot))
    assert message.replies == ["✅ Reported to the admins."]
    assert bot.sent[0][0] == -1009876543210
    assert "REPORT" in bot.sent[0][1]
    rows = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert rows[-1]["rule"] == "user_report"
    assert rows[-1]["user_id"] == TARGET
    assert rows[-1]["applied"] is True
    assert rows[-1]["matched"].startswith("https://t.me/c/")


async def test_no_report_action_against_the_target(tmp_path):
    enable_modlog(tmp_path)
    message = FakeMessage(reply_to=target_message())
    bot = FakeBot()
    await report.handle_group_report(make_update(message), context(bot))
    # A report must never punish: the only API call is the modlog message.
    assert [call for call in bot.sent if call[0] == CHAT] == []


async def test_throttle_after_three(tmp_path):
    enable_modlog(tmp_path)
    bot = FakeBot()
    for _ in range(report.REPORTS_PER_WINDOW):
        message = FakeMessage(reply_to=target_message())
        await report.handle_group_report(make_update(message), context(bot))
    blocked = FakeMessage(reply_to=target_message())
    await report.handle_group_report(make_update(blocked), context(bot))
    assert "already" in blocked.replies[0].lower()
    assert len(bot.sent) == report.REPORTS_PER_WINDOW


async def test_unconfigured_chat_tells_the_member():
    message = FakeMessage(reply_to=target_message())
    bot = FakeBot()
    await report.handle_group_report(make_update(message), context(bot))
    assert "no modlog configured" in message.replies[0]
    assert bot.sent == []
