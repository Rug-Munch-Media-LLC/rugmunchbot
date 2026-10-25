"""``/modraid`` — the admin lever for the reversible raid lockdown.

The detector lives in ``raidmode.py`` and the durable state in ``raid_state.py``;
this module is only the command surface, so a wiring change can never take the
containment logic with it (the 2026-10-25 audit found ``register.py`` importing
a ``cmd_modraid`` that no longer existed, which made the whole moderation
package — and therefore the bot — unimportable).

Gate first, always: a non-admin must be refused BEFORE any state is read or
written. Dry-run chats never engage (a rehearsal must not mute a live room).
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.moderation import raid_state
from rugmunchbot.moderation.config import load_chat_policy
from rugmunchbot.moderation.guard_settings import from_env

USAGE = "Usage: /modraid on [reason] · /modraid off · /modraid status"


async def cmd_modraid(update: Any, context: Any) -> None:
    """Admin: show, engage or lift this chat's raid lockdown."""
    from rugmunchbot.moderation.commands import _allowed

    chat = update.effective_chat
    message = update.effective_message
    if chat is None or message is None:
        return
    if not await _allowed(update, context):
        await message.reply_text("⛔ Chat admins only.")
        return

    args = [str(a).lower() for a in (context.args or [])]
    action = args[0] if args else "status"
    if action == "off":
        changed = raid_state.release(chat.id, actor=f"admin:{update.effective_user.id}")
        await message.reply_text(
            "🔓 Raid lockdown lifted." if changed else "No active raid lockdown."
        )
        return
    if action == "on":
        await _engage(update, chat.id, args)
        return
    if action != "status":
        await message.reply_text(USAGE)
        return
    await _report(chat.id, message)


async def _engage(update: Any, chat_id: int, args: list[str]) -> None:
    """Engage for this chat; audited, and a no-op on a dry-run chat."""
    chat_policy = load_chat_policy(chat_id)
    dry_run = True if chat_policy is None else chat_policy.dry_run
    reason = " ".join(args[1:])[:120] or "manual lockdown by admin"
    raid_state.engage(chat_id, reason, from_env(), dry_run=dry_run)
    await update.effective_message.reply_text(
        "🧪 Raid lockdown rehearsed (dry-run chat, nothing muted)."
        if dry_run
        else f"🔒 Raid lockdown engaged — {reason}."
    )


async def _report(chat_id: int, message: Any) -> None:
    """Render the current state (or the absence of one)."""
    current = raid_state.status(chat_id)
    if current is None:
        await message.reply_text("✅ No raid lockdown active.")
        return
    await message.reply_text(
        f"🔒 Raid lockdown active — {current['reason']}\n"
        f"expires in {current['expires_in_s']}s · lift with /modraid off"
    )


__all__ = ["cmd_modraid"]
