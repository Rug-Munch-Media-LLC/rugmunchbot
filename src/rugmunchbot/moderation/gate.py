"""The single moderation entry point (registered in handler group -1).

Group -1 runs before every other handler, so a deleted spam message never
reaches the AI chat pipeline. When a decision blocks the message we raise
``ApplicationHandlerStop``; a warn-only or alert decision does not block.
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot.moderation import addresses, captcha
from rugmunchbot.moderation.actions import execute
from rugmunchbot.moderation.config import load_chat_policy
from rugmunchbot.moderation.decide import decide as decide_message
from rugmunchbot.moderation.models import MsgContext
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.gate")

_ADMIN_TTL_S = 300
_admin_cache: dict[tuple[int, int], tuple[float, bool]] = {}
_SCAN_BUDGET = 6
_scan_windows: dict[int, list[float]] = {}
_MEDIA = ("photo", "video", "animation", "document")


def _is_media(message: Any) -> str | None:
    for kind in _MEDIA:
        if getattr(message, kind, None):
            return kind
    return None


def _urls(message: Any, text: str) -> tuple[str, ...]:
    from rugmunchbot.moderation import links

    extra: list[str] = []
    for entity in message.entities or ():
        if entity.type in {"url", "text_link"}:
            try:
                extra.append(entity.url or message.parse_entity(entity))
            except Exception:
                logger.debug("unparsable message entity", exc_info=True)
                continue
    return links.extract_urls(text, tuple(extra))


async def _is_admin(bot: Any, chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    cached = _admin_cache.get(key)
    if cached and time.monotonic() - cached[0] < _ADMIN_TTL_S:
        return cached[1]
    try:
        from telegram.constants import ChatMemberStatus

        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        is_admin = member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
    except Exception:
        is_admin = False
    _admin_cache[key] = (time.monotonic(), is_admin)
    return is_admin


def _scan_allowed(chat_id: int) -> bool:
    now = time.monotonic()
    window = [t for t in _scan_windows.get(chat_id, []) if now - t < 60]
    if len(window) >= _SCAN_BUDGET:
        _scan_windows[chat_id] = window
        return False
    window.append(now)
    _scan_windows[chat_id] = window
    return True


async def build_context(update: Any, bot: Any) -> MsgContext | None:
    """Normalise an update into a MsgContext, or None when not a chat message."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or user.is_bot:
        return None
    text = message.text or message.caption or ""
    return MsgContext(
        chat_id=chat.id,
        chat_title=chat.title or chat.username or "",
        user_id=user.id,
        username=user.username or "",
        display_name=user.full_name or "",
        text=text,
        urls=_urls(message, text),
        media=_is_media(message),
        is_admin=await _is_admin(bot, chat.id, user.id),
        message_id=message.message_id,
    )


async def handle_message(update: Any, context: Any) -> bool:
    """Run the message rules. Returns True when the update was consumed."""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return False
    policy = load_chat_policy(chat.id)
    if policy is None:
        return False
    if (message.text or "").startswith("/"):
        return False  # commands are the commands module's business
    ctx = await build_context(update, context.bot)
    if ctx is None:
        return False

    verdicts = {}
    if ctx.text and _scan_allowed(chat.id):
        verdicts = await addresses.assess(ctx.text)
    decision = decide_message(ctx, policy, verdicts)
    if decision is None:
        return False
    record = await execute(context.bot, ctx, policy, decision)
    logger.info(
        "moderation decision",
        extra={
            "chat_id": ctx.chat_id,
            "rule": decision.rule,
            "action": decision.action,
            "dry_run": policy.dry_run,
            "applied": record.get("applied"),
        },
    )
    return decision.blocks_pipeline


async def moderation_gate(update: Any, context: Any) -> None:
    """Handler(group=-1): route joins, captcha taps and messages."""
    from telegram.ext import ApplicationHandlerStop

    consumed = False
    if update.message is not None and update.message.new_chat_members:
        consumed = await captcha.handle_new_members(update, context)
    elif update.callback_query is not None:
        consumed = await captcha.handle_captcha_callback(update, context)
    elif update.edited_message is not None:
        consumed = False  # edits are audited only when a rule re-fires; keep cheap
    else:
        consumed = await handle_message(update, context)
    if consumed:
        raise ApplicationHandlerStop


__all__ = ["build_context", "handle_message", "moderation_gate"]
