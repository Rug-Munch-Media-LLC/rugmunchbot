"""Welcome and goodbye messages (opt-in per chat, plain text, never blocks).

Texts are admin-provided, so they are sent **without** ``parse_mode``: a
template can never inject HTML or a link preview, and ``{name}``/``{chat}`` are
the only substitutions. Nothing is sent when the feature is off, which is the
default for every chat.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.moderation import audit, chatconf
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.welcome")

MAX_NAME = 64


def render(template: str, name: str, chat_title: str) -> str:
    """Substitute the two supported placeholders; everything else is literal."""
    return (
        (template or "")
        .replace("{name}", (name or "friend")[:MAX_NAME])
        .replace("{chat}", (chat_title or "")[:MAX_NAME])
    )[: chatconf.MAX_TEXT]


async def send(bot: Any, chat_id: int, text: str) -> bool:
    """Post one greeting. Failures are logged, never raised to the dispatcher."""
    if not text.strip():
        return False
    try:
        await bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        return True
    except Exception:  # missing rights must not break the gate
        logger.info("welcome/goodbye send failed", exc_info=True)
        return False


async def _record(chat_id: int, user_id: int, rule: str, applied: bool, dry: bool) -> None:
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat_id,
            chat_title="",
            user_id=user_id,
            username="",
            rule=rule,
            action="message",
            reason="community template",
            matched="",
            dry_run=dry,
            applied=applied,
            actor="auto:community",
        )
    )


async def handle_join(update: Any, context: Any) -> bool:
    """Greet every human joiner when enabled. Returns True when anything was sent."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not message.new_chat_members:
        return False
    cfg = await chatconf.effective(chat.id)
    if not cfg.welcome_enabled or not cfg.welcome_text.strip():
        return False
    sent = False
    for member in message.new_chat_members:
        if getattr(member, "is_bot", False):
            continue
        name = getattr(member, "full_name", "") or getattr(member, "first_name", "")
        ok = await send(context.bot, chat.id, render(cfg.welcome_text, name, chat.title or ""))
        await _record(chat.id, member.id, "welcome", ok, False)
        sent = sent or ok
    return sent


async def handle_leave(update: Any, context: Any) -> bool:
    """Say goodbye to a departing member when enabled."""
    message, chat = update.effective_message, update.effective_chat
    left = getattr(message, "left_chat_member", None) if message is not None else None
    if left is None or chat is None or getattr(left, "is_bot", False):
        return False
    cfg = await chatconf.effective(chat.id)
    if not cfg.goodbye_enabled or not cfg.goodbye_text.strip():
        return False
    name = getattr(left, "full_name", "") or getattr(left, "first_name", "")
    ok = await send(context.bot, chat.id, render(cfg.goodbye_text, name, chat.title or ""))
    await _record(chat.id, left.id, "goodbye", ok, False)
    return ok


__all__ = ["handle_join", "handle_leave", "render", "send"]
