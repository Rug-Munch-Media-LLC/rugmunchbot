"""Admin commands that change community settings: welcome, goodbye, overview.

Overrides are stored per chat (Redis/memory) rather than written back into
``configs/moderation.json``: the JSON stays the operator's opt-in ceiling, and the
bot never rewrites its own config file. Every change is audited. A command is
refused when the feature is not enabled in the JSON — flipping that flag stays a
deliberate operator action.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.moderation import audit, auth, chatconf, store

KIND = "settings"
MAX_TEXT = chatconf.MAX_TEXT

_FIELDS = {
    "welcome": ("welcome_text", "welcome_enabled"),
    "goodbye": ("goodbye_text", "goodbye_enabled"),
}


def _record(update: Any, field: str, ok: bool) -> None:
    user = update.effective_user
    chat = update.effective_chat
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat.id if chat else 0,
            chat_title="",
            user_id=user.id if user else 0,
            username=getattr(user, "username", "") or "",
            rule="community_config",
            action="set",
            reason=field,
            matched=field,
            dry_run=False,
            applied=ok,
            actor=f"admin:{user.id}" if user else "auto",
        )
    )


async def _set_text(update: Any, context: Any, kind: str) -> None:
    """Shared body of /setwelcome and /setgoodbye."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    field, flag = _FIELDS[kind]
    body = message.text.split(maxsplit=1)[1].strip()[:MAX_TEXT] if message.text else ""
    if not body:
        await message.reply_text(f"Usage: /set{kind} <text>  (placeholders: {{name}}, {{chat}})")
        return
    if not getattr(chatconf.load_community_config(chat.id), flag):
        await message.reply_text(
            f"{kind.title()} messages are disabled for this chat; an operator must enable "
            f"community.{flag} first."
        )
        return
    ok = await store.set_field(store.chat_key(chat.id, KIND), field, body)
    _record(update, field, ok)
    await message.reply_text(f"✅ {kind.title()} text saved." if ok else "⚠️ Save failed.")


async def cmd_setwelcome(update: Any, context: Any) -> None:
    """``/setwelcome <text>`` — store this chat's welcome template."""
    await _set_text(update, context, "welcome")


async def cmd_setgoodbye(update: Any, context: Any) -> None:
    """``/setgoodbye <text>`` — store this chat's goodbye template."""
    await _set_text(update, context, "goodbye")


async def cmd_modconfig(update: Any, context: Any) -> None:
    """``/modconfig`` — show the effective community settings (admins only)."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    cfg = await chatconf.effective(chat.id)
    flags = [
        f"welcome: {'on' if cfg.welcome_enabled else 'off'} ({len(cfg.welcome_text)} chars)",
        f"goodbye: {'on' if cfg.goodbye_enabled else 'off'} ({len(cfg.goodbye_text)} chars)",
        f"filters: {'on' if cfg.filters_enabled else 'off'} · "
        f"{cfg.filter_replies_per_min}/min/user",
        f"gban enforcement: {'on' if cfg.enforce_gban else 'off'}",
        f"scheduled: {'on' if cfg.scheduled_enabled else 'off'} · max {cfg.schedule_max}",
        f"notes admin-only: {'yes' if cfg.notes_admins_only else 'no'}",
    ]
    await message.reply_text(
        "⚙️ <b>Community config</b>\n"
        + "\n".join(flags)
        + "\n\nJSON is the opt-in ceiling: edit configs/moderation.json to flip a feature on.",
        parse_mode="HTML",
    )


__all__ = ["cmd_modconfig", "cmd_setgoodbye", "cmd_setwelcome"]
