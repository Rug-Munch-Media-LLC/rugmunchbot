"""Member-facing ``/report`` inside a group: flag a message for the admins.

Distinct from the DM ``/report <address>`` scam intake in
``handlers/report.py`` (that one feeds the shared scam denylist). This module
handles the group shape: a member replies to a message with ``/report [why]``
and the report is written to the audit trail and mirrored into the chat's
modlog so a human can act.

Safety properties:

* never takes an action against the reported user — a report is evidence, not
  a verdict (only humans or the rule engine may punish);
* rate-limited per (chat, user) so a raid cannot flood the modlog;
* when no modlog is configured the reporter is told so — a member must never
  believe they reported something that nobody will read.
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot.moderation import audit, chatconf, modlog

REPORTS_PER_WINDOW = 3
WINDOW_S = 600
MAX_REASON = 240

_seen: dict[tuple[int, int], list[float]] = {}


def _throttled(chat_id: int, user_id: int, now: float | None = None) -> bool:
    """True when this member already used their report budget."""
    ts = time.monotonic() if now is None else now
    bucket = [t for t in _seen.get((chat_id, user_id), []) if ts - t < WINDOW_S]
    _seen[(chat_id, user_id)] = bucket
    if len(bucket) >= REPORTS_PER_WINDOW:
        return True
    bucket.append(ts)
    if len(_seen) > 5000:  # bounded memory under a raid
        for key in [k for k, v in _seen.items() if not v]:
            _seen.pop(key, None)
    return False


def _message_link(chat_id: int, message_id: int) -> str:
    """Best-effort t.me permalink for supergroups; empty otherwise."""
    raw = str(chat_id)
    if not raw.startswith("-100"):
        return ""
    return f"https://t.me/c/{raw[4:]}/{message_id}"


async def handle_group_report(update: Any, context: Any) -> None:
    """``/report [reason]`` as a reply inside a group. Always answers."""
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    target = getattr(message, "reply_to_message", None)
    if target is None:
        await message.reply_text(
            "Reply to the message you want to report, then send /report [reason]."
        )
        return
    if _throttled(chat.id, user.id):
        await message.reply_text(
            "You have sent several reports already — an admin will review them."
        )
        return
    cfg = await chatconf.effective(chat.id)
    reason = (message.text or "").split(maxsplit=1)
    reason = reason[1].strip()[:MAX_REASON] if len(reason) > 1 else ""
    reported = getattr(target, "from_user", None)
    entry = audit.build_entry(
        action_id=audit.new_action_id(),
        chat_id=chat.id,
        chat_title=chat.title or "",
        user_id=getattr(reported, "id", 0) or 0,
        username=getattr(reported, "username", "") or "",
        rule="user_report",
        action="report",
        reason=reason or "no reason given",
        matched=_message_link(chat.id, getattr(target, "message_id", 0) or 0),
        dry_run=False,
        applied=False,
        actor=f"user:{user.id}",
        text=getattr(target, "text", "") or getattr(target, "caption", "") or "",
    )
    delivered = await modlog.deliver(
        context.bot, entry, chat_id=chat.id, cfg=cfg, title=chat.title or ""
    )
    entry["applied"] = delivered
    audit.record(entry)
    if delivered:
        await message.reply_text("✅ Reported to the admins.")
    else:
        await message.reply_text(
            "⚠️ This chat has no modlog configured — please message an admin directly."
        )


__all__ = ["handle_group_report"]
