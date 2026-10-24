"""Member-facing ``/report`` inside a group: flag a message for the admins.

Distinct from the DM ``/report <address>`` scam intake in
``handlers/report.py`` (that one feeds the shared scam denylist). This module
owns the group shape: a member replies to a message with ``/report [why]`` and
the report reaches the chat's modlog, where a human can act on it.

Safety properties:

* never takes an action against the reported user — a report is evidence, not
  a verdict (only humans or the rule engine may punish);
* rate-limited per (chat, user) so a raid cannot flood the modlog;
* when no modlog is configured the reporter is told so — a member must never
  believe they filed something nobody will read.
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot.moderation import audit, chatconf, modlog

REPORTS_PER_WINDOW = 3
WINDOW_S = 600
MAX_REASON = 240
MAX_TRACKED = 5000

NO_REPLY = "Reply to the message you want to report, then send /report [reason]."
THROTTLED = "You have sent several reports already — an admin will review them."
DELIVERED = "✅ Reported to the admins."
NO_MODLOG = "⚠️ This chat has no modlog configured — please message an admin directly."
GROUPS = {"group", "supergroup"}

_seen: dict[tuple[int, int], list[float]] = {}


def _extract(update: Any) -> tuple[Any, Any, Any, Any] | None:
    """Unpack the four objects a report needs, or None when incomplete."""
    message = getattr(update, "effective_message", None)
    chat = getattr(update, "effective_chat", None)
    user = getattr(update, "effective_user", None)
    if message is None or chat is None or user is None:
        return None
    return message, chat, user, getattr(message, "reply_to_message", None)


def _throttled(chat_id: int, user_id: int, now: float | None = None) -> bool:
    """True when this member already used their report budget."""
    ts = time.monotonic() if now is None else now
    key = (chat_id, user_id)
    bucket = [t for t in _seen.get(key, []) if ts - t < WINDOW_S]
    _seen[key] = bucket
    if len(bucket) >= REPORTS_PER_WINDOW:
        return True
    bucket.append(ts)
    if len(_seen) > MAX_TRACKED:  # bounded memory under a raid
        for stale in [k for k, v in _seen.items() if not v]:
            _seen.pop(stale, None)
    return False


def _message_link(chat_id: int, message_id: int) -> str:
    """Best-effort t.me permalink for supergroups; empty otherwise."""
    raw = str(chat_id)
    return f"https://t.me/c/{raw[4:]}/{message_id}" if raw.startswith("-100") else ""


def _reason(message: Any) -> str:
    """The reporter's own words, after the command word."""
    parts = (getattr(message, "text", "") or "").split(maxsplit=1)
    return (parts[1].strip()[:MAX_REASON] if len(parts) > 1 else "") or "no reason given"


def _entry(chat: Any, message: Any, user: Any, target: Any) -> dict[str, Any]:
    """Build the audit record for one report (pure, no I/O)."""
    reported = getattr(target, "from_user", None)
    text = getattr(target, "text", "") or getattr(target, "caption", "") or ""
    return audit.build_entry(
        action_id=audit.new_action_id(),
        chat_id=chat.id,
        chat_title=getattr(chat, "title", "") or "",
        user_id=getattr(reported, "id", 0) or 0,
        username=getattr(reported, "username", "") or "",
        rule="user_report",
        action="report",
        reason=_reason(message),
        matched=_message_link(chat.id, getattr(target, "message_id", 0) or 0),
        dry_run=False,
        applied=False,
        actor=f"user:{user.id}",
        text=text,
    )


async def handle_group_report(update: Any, context: Any) -> bool:
    """``/report [reason]`` as a reply inside a group. True when handled here."""
    parts = _extract(update)
    if parts is None:
        return False
    message, chat, user, target = parts
    if getattr(chat, "type", "") not in GROUPS:
        return False
    if target is None:
        await message.reply_text(NO_REPLY)
        return True
    if _throttled(chat.id, user.id):
        await message.reply_text(THROTTLED)
        return True
    entry = _entry(chat, message, user, target)
    delivered = await modlog.deliver(
        context.bot,
        entry,
        chat_id=chat.id,
        cfg=await chatconf.effective(chat.id),
        title=getattr(chat, "title", "") or "",
    )
    entry["applied"] = delivered
    audit.record(entry)
    await message.reply_text(DELIVERED if delivered else NO_MODLOG)
    return True


async def group_report_command(update: Any, context: Any) -> None:
    """Group ``-2`` handler: handle group reports, let DMs fall through."""
    from telegram.ext import ApplicationHandlerStop

    if await handle_group_report(update, context):
        raise ApplicationHandlerStop


__all__ = ["group_report_command", "handle_group_report"]
