"""Global ban list (bot owners) and opt-in enforcement.

The list is global — one entry bans a user in every chat that opts in. Two
gates protect against accidental auto-bans:

1. ``community.enforce_gban`` must be ``true`` in that chat's config (default
   false), and
2. the chat must be live (``dry_run`` false); a dry-run chat only records the
   would-be ban in the audit trail.

Chat administrators are never auto-banned by this path. Only bot owners can
change the list, and every change is audited.
"""

from __future__ import annotations

import json
import time
from typing import Any

from rugmunchbot.moderation import audit, auth, store
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.gban")

KIND = "gbans"
MAX_REASON = 200


def target_of(update: Any, context: Any) -> int | None:
    """Resolve the target user: replied-to message first, then the first argument."""
    reply = getattr(update.effective_message, "reply_to_message", None)
    sender = getattr(reply, "from_user", None)
    if sender is not None:
        return int(sender.id)
    for arg in context.args or []:
        candidate = str(arg).strip()
        if candidate.lstrip("-").isdigit():
            return int(candidate)
    return None


async def _record(update: Any, user_id: int, action: str, reason: str, ok: bool) -> None:
    actor = update.effective_user
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=update.effective_chat.id if update.effective_chat else 0,
            chat_title="global",
            user_id=user_id,
            username="",
            rule="gban",
            action=action,
            reason=reason,
            matched="global",
            dry_run=False,
            applied=ok,
            actor=f"owner:{actor.id}" if actor else "owner",
        )
    )


async def cmd_gban(update: Any, context: Any) -> None:
    """``/gban <user_id|reply> [reason]`` — owner-only add to the global list."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or not auth.is_owner(update):
        if message is not None:
            await message.reply_text("⛔ Bot owners only.")
        return
    user_id = target_of(update, context)
    if user_id is None:
        await message.reply_text("Usage: /gban <user_id> [reason] (or reply to a message)")
        return
    reason = " ".join(
        context.args[1:] if update.effective_message.reply_to_message is None else context.args
    )
    entry = json.dumps(
        {
            "reason": reason[:MAX_REASON] or "owner gban",
            "by": update.effective_user.id,
            "ts": int(time.time()),
        }
    )
    ok = await store.set_field(store.global_key(KIND), str(user_id), entry, max_fields=10_000)
    await _record(update, user_id, "gban", reason, ok)
    await message.reply_text(
        f"🔨 <code>{user_id}</code> globally banned." if ok else "⚠️ Global list is full.",
        parse_mode="HTML",
    )
    logger.info("global ban added", extra={"chat_id": chat.id if chat else 0, "target": user_id})


async def cmd_ungban(update: Any, context: Any) -> None:
    """``/ungban <user_id>`` — owner-only removal from the global list."""
    message = update.effective_message
    if message is None or not auth.is_owner(update):
        if message is not None:
            await message.reply_text("⛔ Bot owners only.")
        return
    user_id = target_of(update, context)
    if user_id is None:
        await message.reply_text("Usage: /ungban <user_id>")
        return
    ok = await store.del_field(store.global_key(KIND), str(user_id))
    await _record(update, user_id, "ungban", "owner removal", ok)
    await message.reply_text(
        f"✅ <code>{user_id}</code> removed from the global ban list."
        if ok
        else f"<code>{user_id}</code> was not on the list.",
        parse_mode="HTML",
    )


async def cmd_gbans(update: Any, context: Any) -> None:
    """``/gbans`` — owner-only listing (id: reason)."""
    message = update.effective_message
    if message is None:
        return
    if not auth.is_owner(update):
        await message.reply_text("⛔ Bot owners only.")
        return
    entries = await store.all_fields(store.global_key(KIND))
    if not entries:
        await message.reply_text("Global ban list is empty.")
        return
    lines = ["🔨 <b>Global bans</b>"]
    for user_id, payload in sorted(entries.items())[:30]:
        try:
            reason = str(json.loads(payload).get("reason", ""))[:60]
        except ValueError:
            reason = ""
        lines.append(f"<code>{user_id}</code> {reason}")
    await message.reply_text("\n".join(lines), parse_mode="HTML")


async def enforce(bot: Any, ctx: Any, policy: Any, cfg: Any) -> bool:
    """Ban a globally-listed user when this chat opted in. Returns True if consumed."""
    if not cfg.enforce_gban:
        return False
    entry = await store.get_field(store.global_key(KIND), str(ctx.user_id))
    if entry is None:
        return False
    if ctx.is_admin:
        return False
    dry = bool(policy.dry_run)
    applied = False
    if not dry:
        try:
            await bot.ban_chat_member(chat_id=ctx.chat_id, user_id=ctx.user_id)
            if ctx.message_id:
                await bot.delete_message(chat_id=ctx.chat_id, message_id=ctx.message_id)
            applied = True
        except Exception:  # missing rights must not break the gate
            logger.info("gban enforcement failed", exc_info=True)
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=ctx.chat_id,
            chat_title=ctx.chat_title,
            user_id=ctx.user_id,
            username=ctx.username,
            rule="gban",
            action="ban",
            reason="globally banned user",
            matched=entry[:MAX_REASON],
            dry_run=dry,
            applied=applied,
            actor="auto:gban",
        )
    )
    return applied


__all__ = ["cmd_gban", "cmd_gbans", "cmd_ungban", "enforce", "target_of"]
