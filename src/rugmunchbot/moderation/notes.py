"""Saved notes: ``/save``, ``/get``, ``/notes``, ``/clear``.

Parity with MissRose/Heimdall, scoped to one chat. Names are normalised to
``[a-z0-9_-]`` so a note can never shadow a command or inject markup; bodies are
stored verbatim and sent as plain text (no ``parse_mode``). Writing is admin-only
when ``community.notes_admins_only`` is on, which is the default; reading is open.
"""

from __future__ import annotations

import re
from typing import Any

from rugmunchbot.moderation import audit, auth, chatconf, store

NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
MAX_TEXT = 1000
MAX_NOTES = 100
KIND = "notes"


def note_name(raw: str) -> str | None:
    """Normalise a user-supplied note name, or None when it is not usable."""
    name = (raw or "").strip().lower().lstrip("#")
    return name if NAME_RE.match(name) else None


async def note_body(update: Any) -> str:
    """The note text: this command's arguments, else the replied-to message."""
    message = update.effective_message
    reply = getattr(message, "reply_to_message", None) if message is not None else None
    if reply is not None and (reply.text or reply.caption):
        return (reply.text or reply.caption or "")[:MAX_TEXT]
    parts = (message.text or "").split(maxsplit=2) if message is not None else []
    return parts[2][:MAX_TEXT] if len(parts) > 2 else ""


async def _record(update: Any, chat_id: int, rule: str, matched: str, ok: bool) -> None:
    user = update.effective_user
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat_id,
            chat_title="",
            user_id=user.id if user else 0,
            username=getattr(user, "username", "") or "",
            rule=f"note_{rule}",
            action=rule,
            reason="notes",
            matched=matched,
            dry_run=False,
            applied=ok,
            actor=f"admin:{user.id}" if user else "auto",
        )
    )


async def cmd_save(update: Any, context: Any) -> None:
    """``/save <name> [text]`` — store or overwrite a note."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None:
        return
    cfg = chatconf.load_community_config(chat.id)
    if cfg.notes_admins_only and not await auth.require_admin(update, context):
        return
    name = note_name((context.args or [""])[0])
    if name is None:
        await message.reply_text("Usage: /save <name> [text] (or reply to a message)")
        return
    body = await note_body(update)
    if not body:
        await message.reply_text("Nothing to save: add text or reply to a message.")
        return
    ok = await store.set_field(store.chat_key(chat.id, KIND), name, body, max_fields=MAX_NOTES)
    await _record(update, chat.id, "save", name[:64], ok)
    await message.reply_text(
        f"✅ Saved <b>#{name}</b>." if ok else "⚠️ Note limit reached; delete one first.",
        parse_mode="HTML",
    )


async def cmd_get(update: Any, context: Any) -> None:
    """``/get <name>`` — post a saved note (open to every member)."""
    message, chat = update.effective_message, update.effective_chat
    name = note_name((context.args or [""])[0])
    if message is None or chat is None or name is None:
        if message is not None:
            await message.reply_text("Usage: /get <name>")
        return
    body = await store.get_field(store.chat_key(chat.id, KIND), name)
    if body is None:
        await message.reply_text(f"No note <b>#{name}</b> here. Try /notes.", parse_mode="HTML")
        return
    await message.reply_text(body, disable_web_page_preview=True)


async def cmd_notes(update: Any, context: Any) -> None:
    """``/notes`` — list this chat's note names."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None:
        return
    names = sorted(await store.all_fields(store.chat_key(chat.id, KIND)))
    if not names:
        await message.reply_text("No notes yet. Admins: /save <name> <text>.")
        return
    await message.reply_text(
        f"🗒️ <b>Notes</b> ({len(names)}): " + ", ".join(f"#{n}" for n in names[:MAX_NOTES]),
        parse_mode="HTML",
    )


async def cmd_clear(update: Any, context: Any) -> None:
    """``/clear <name>`` — admin-only note deletion."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    name = note_name((context.args or [""])[0])
    if name is None:
        await message.reply_text("Usage: /clear <name>")
        return
    ok = await store.del_field(store.chat_key(chat.id, KIND), name)
    await _record(update, chat.id, "clear", name[:64], ok)
    await message.reply_text(
        f"🗑️ Deleted <b>#{name}</b>." if ok else f"No note <b>#{name}</b>.", parse_mode="HTML"
    )


__all__ = ["cmd_clear", "cmd_get", "cmd_notes", "cmd_save", "note_name"]
