"""Per-chat rules — ``/rules``, ``/setrules``, ``/clearrules`` (offline)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from telegram.constants import ChatMemberStatus

from rugmunchbot.moderation import audit, config, rules_cmd, store

CHAT = -1001234567890
ADMIN = 424242
MEMBER = 777


class FakeBot:
    def __init__(self, *, admin: bool = True) -> None:
        self.status = ChatMemberStatus.ADMINISTRATOR if admin else ChatMemberStatus.MEMBER

    async def get_chat_member(self, chat_id: int, user_id: int):
        return SimpleNamespace(status=self.status)


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


def make_update(text: str, *, user_id: int = ADMIN):
    return SimpleNamespace(
        effective_message=FakeMessage(text),
        effective_chat=SimpleNamespace(id=CHAT, title="Rug Munch", type="supergroup"),
        effective_user=SimpleNamespace(id=user_id, username="admin"),
    )


def context(*, admin: bool = True):
    return SimpleNamespace(bot=FakeBot(admin=admin))


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


def configure(tmp_path, **community) -> None:
    cfg_file = tmp_path / "moderation.json"
    cfg_file.write_text(
        json.dumps({"chats": {str(CHAT): {"community": community}}}), encoding="utf-8"
    )
    config.use_config_path(cfg_file)


def test_builtin_text_for_unguarded_chat():
    text = rules_cmd.builtin_text(None)
    assert "Chat rules" in text
    # member-facing: no operator config path, no "enforcement is off" hint
    assert "configs/moderation.json" not in text
    assert "not guarded" not in text


def test_builtin_text_reflects_mode():
    observing = SimpleNamespace(dry_run=True, dup_count=3)
    live = SimpleNamespace(dry_run=False, dup_count=3)
    assert "dry-run" in rules_cmd.builtin_text(observing)
    assert "enforcing" in rules_cmd.builtin_text(live)


def test_rules_text_is_truncated_by_config():
    from rugmunchbot.moderation.chatconf import MAX_TEXT, CommunityConfig

    cfg = CommunityConfig.from_dict(CHAT, {"community": {"rules_text": "x" * (MAX_TEXT + 50)}})
    assert len(cfg.rules_text) == MAX_TEXT


async def test_rules_shows_builtin_when_no_override():
    update = make_update("/rules")
    await rules_cmd.cmd_rules(update, context())
    assert "Chat rules" in update.effective_message.replies[0]


async def test_setrules_refused_until_operator_opts_in():
    update = make_update("/setrules Be nice")
    await rules_cmd.cmd_setrules(update, context())
    assert "disabled for this chat" in update.effective_message.replies[0]
    assert await store.all_fields(store.chat_key(CHAT, rules_cmd.KIND)) == {}


async def test_setrules_persists_and_rules_shows_it(tmp_path):
    configure(tmp_path, rules_enabled=True)
    written = make_update("/setrules No shilling. Report with /report.")
    await rules_cmd.cmd_setrules(written, context())
    assert written.effective_message.replies == ["✅ Rules saved."]
    shown = make_update("/rules")
    await rules_cmd.cmd_rules(shown, context())
    assert shown.effective_message.replies[0] == "No shilling. Report with /report."
    rows = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert rows[-1]["actor"] == f"admin:{ADMIN}"
    assert rows[-1]["applied"] is True


async def test_setrules_needs_admin(tmp_path):
    configure(tmp_path, rules_enabled=True)
    update = make_update("/setrules pwn", user_id=MEMBER)
    await rules_cmd.cmd_setrules(update, context(admin=False))
    assert "admins only" in update.effective_message.replies[0].lower()
    assert await store.all_fields(store.chat_key(CHAT, rules_cmd.KIND)) == {}


async def test_clearrules_restores_builtin(tmp_path):
    configure(tmp_path, rules_enabled=True)
    await rules_cmd.cmd_setrules(make_update("/setrules Custom"), context())
    cleared = make_update("/clearrules")
    await rules_cmd.cmd_clearrules(cleared, context())
    assert "removed" in cleared.effective_message.replies[0]
    shown = make_update("/rules")
    await rules_cmd.cmd_rules(shown, context())
    assert "Chat rules" in shown.effective_message.replies[0]
