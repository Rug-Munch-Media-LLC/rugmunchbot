"""Scheduled messages: ``/schedule``, ``/schedules``, ``/unschedule`` + minute tick.

Times are UTC ``HH:MM``, repeating daily. A post is keyed by chat + minute so a
restart or a double tick cannot double-post. Nothing is scheduled or posted until
the chat sets ``community.scheduled_enabled`` true (default false) and an admin
adds an entry.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.moderation import audit, auth, chatconf, config, store
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.schedule")

KIND = "sched"
MAX_TEXT = 500
_STAMP = "%Y-%m-%dT%H:%M"


def parse_time(raw: str) -> str | None:
    """Accept ``H:MM``/``HH:MM`` (24h, UTC); return zero-padded ``HH:MM``."""
    parts = (raw or "").strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    hour, minute = int(parts[0]), int(parts[1])
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def _record(update: Any, rule: str, matched: str, ok: bool) -> None:
    user = update.effective_user
    chat = update.effective_chat
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat.id if chat else 0,
            chat_title="",
            user_id=user.id if user else 0,
            username=getattr(user, "username", "") or "",
            rule=f"schedule_{rule}",
            action=rule,
            reason="scheduled message",
            matched=matched[:120],
            dry_run=False,
            applied=ok,
            actor=f"admin:{user.id}" if user else "auto",
        )
    )


async def cmd_schedule(update: Any, context: Any) -> None:
    """``/schedule HH:MM <text>`` — add a daily post (admins, opt-in chats)."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    cfg = chatconf.load_community_config(chat.id)
    if not cfg.scheduled_enabled:
        await message.reply_text("Scheduled messages are disabled for this chat.")
        return
    args = (message.text or "").split(maxsplit=2)
    when = parse_time(args[1]) if len(args) > 1 else None
    text = args[2].strip()[:MAX_TEXT] if len(args) > 2 else ""
    if when is None or not text:
        await message.reply_text("Usage: /schedule HH:MM <text> (UTC, daily)")
        return
    wait = len(await store.all_fields(store.chat_key(chat.id, KIND)))
    if wait >= cfg.schedule_max:
        await message.reply_text(f"Limit is {cfg.schedule_max} scheduled posts; remove one.")
        return
    entry_id = uuid.uuid4().hex[:6]
    ok = await store.set_field(
        store.chat_key(chat.id, KIND),
        entry_id,
        json.dumps({"t": when, "text": text, "last": ""}),
        max_fields=cfg.schedule_max,
    )
    _record(update, "add", f"{entry_id} {when}", ok)
    await message.reply_text(
        f"⏰ Scheduled <code>{entry_id}</code> daily at <b>{when}</b> UTC."
        if ok
        else "⚠️ Could not store the schedule.",
        parse_mode="HTML",
    )


async def cmd_schedules(update: Any, context: Any) -> None:
    """``/schedules`` — list this chat's scheduled posts."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None:
        return
    entries = await store.all_fields(store.chat_key(chat.id, KIND))
    if not entries:
        await message.reply_text("No scheduled messages here.")
        return
    lines = ["⏰ <b>Scheduled (UTC)</b>"]
    for entry_id, payload in sorted(entries.items(), key=lambda kv: _when(kv[1])):
        try:
            row = json.loads(payload)
        except ValueError:
            continue
        lines.append(f"<code>{entry_id}</code> {row.get('t')} — {str(row.get('text'))[:60]}")
    await message.reply_text("\n".join(lines), parse_mode="HTML")


def _when(payload: str) -> str:
    try:
        return str(json.loads(payload).get("t", "99:99"))
    except ValueError:
        return "99:99"


async def cmd_unschedule(update: Any, context: Any) -> None:
    """``/unschedule <id>`` — remove a scheduled post (admins)."""
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None or not await auth.require_admin(update, context):
        return
    entry_id = (context.args or [""])[0].strip().lower()
    ok = await store.del_field(store.chat_key(chat.id, KIND), entry_id) if entry_id else False
    _record(update, "del", entry_id[:32], ok)
    await message.reply_text(
        f"🗑️ Removed <code>{entry_id}</code>." if ok else "No such schedule id.",
        parse_mode="HTML",
    )


async def tick(context: Any, now: datetime | None = None) -> int:
    """Post every schedule due this minute. Returns how many were sent."""
    now = now or datetime.now(UTC)
    stamp, hhmm = now.strftime(_STAMP), now.strftime("%H:%M")
    sent = 0
    for chat_id in config.guarded_chat_ids():
        if not chatconf.load_community_config(chat_id).scheduled_enabled:
            continue
        key = store.chat_key(chat_id, KIND)
        for entry_id, payload in (await store.all_fields(key)).items():
            try:
                row = json.loads(payload)
            except ValueError:
                continue
            if row.get("t") != hhmm or row.get("last") == stamp:
                continue
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=str(row.get("text"))[:MAX_TEXT],
                    disable_web_page_preview=True,
                )
                sent += 1
            except Exception:  # one bad chat must not stop the tick
                logger.info("scheduled send failed", extra={"chat_id": chat_id})
            row["last"] = stamp
            await store.set_field(key, entry_id, json.dumps(row))
    return sent


__all__ = ["cmd_schedule", "cmd_schedules", "cmd_unschedule", "parse_time", "tick"]
