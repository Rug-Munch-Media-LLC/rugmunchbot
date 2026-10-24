"""Raid / flood containment with an explicit, reversible lockdown.

Two sliding windows per chat — joins and messages. Crossing either threshold
ENGAGES a raid state with a reason, a TTL (raid_hold_s) and an audit row. While
active, messages from members who have not passed the entrance gate are held
(see decide.py); verified and trusted members keep talking, so a raid never
silences the room it protects. Every transition is reversible: /modraid off
releases it immediately, and the release is audited — no silent lockdown.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from rugmunchbot.moderation import audit
from rugmunchbot.moderation.guard_settings import GuardSettings
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.raidmode")

MAX_CHATS = 5_000
MAX_EVENTS = 256
_joins: dict[int, deque[float]] = {}
_msgs: dict[int, deque[float]] = {}
_active: dict[int, tuple[float, str]] = {}


@dataclass(slots=True, frozen=True)
class RaidSignal:
    """Outcome of one observed join/message for the raid detector."""

    count: int
    window_s: int
    triggered: bool
    reason: str = ""


def _note(store: dict[int, deque[float]], chat_id: int, window_s: int, now: float) -> int:
    if len(store) > MAX_CHATS:
        store.clear()
    bucket = store.setdefault(chat_id, deque(maxlen=MAX_EVENTS))
    bucket.append(now)
    while bucket and bucket[0] < now - window_s:
        bucket.popleft()
    return len(bucket)


def engage(
    chat_id: int,
    reason: str,
    settings: GuardSettings,
    now: float | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Enter raid state for raid_hold_s and write the audit row."""
    ts = time.time() if now is None else now
    _active[chat_id] = (ts + settings.raid_hold_s, reason)
    entry = audit.build_entry(
        action_id=audit.new_action_id(),
        chat_id=chat_id,
        chat_title="",
        user_id=0,
        username="",
        rule="raid_detected",
        action="lockdown",
        reason=reason,
        matched="",
        dry_run=dry_run,
        applied=not dry_run,
        actor="auto:raid",
        extra={"hold_s": settings.raid_hold_s},
    )
    audit.record(entry)
    logger.warning("raid state engaged", extra={"chat_id": chat_id, "reason": reason})
    return entry


def note_join(
    chat_id: int, settings: GuardSettings, now: float | None = None, *, dry_run: bool = False
) -> RaidSignal:
    """Count a join; engage when raid_joins land inside raid_window_s."""
    ts = time.time() if now is None else now
    count = _note(_joins, chat_id, settings.raid_window_s, ts)
    if count < settings.raid_joins:
        return RaidSignal(count, settings.raid_window_s, False)
    if is_active(chat_id, ts):
        return RaidSignal(count, settings.raid_window_s, True, status(chat_id, ts)["reason"])
    reason = f"{count} joins within {settings.raid_window_s}s"
    engage(chat_id, reason, settings, ts, dry_run=dry_run)
    return RaidSignal(count, settings.raid_window_s, True, reason)


def note_message(
    chat_id: int, settings: GuardSettings, now: float | None = None, *, dry_run: bool = False
) -> RaidSignal:
    """Count a message; engage when flood_msgs land inside flood_window_s."""
    ts = time.time() if now is None else now
    count = _note(_msgs, chat_id, settings.flood_window_s, ts)
    if count < settings.flood_msgs:
        return RaidSignal(count, settings.flood_window_s, False)
    if is_active(chat_id, ts):
        return RaidSignal(count, settings.flood_window_s, True, status(chat_id, ts)["reason"])
    reason = f"{count} messages within {settings.flood_window_s}s"
    engage(chat_id, reason, settings, ts, dry_run=dry_run)
    return RaidSignal(count, settings.flood_window_s, True, reason)


def is_active(chat_id: int, now: float | None = None) -> bool:
    """True while the raid hold has not expired."""
    entry = _active.get(chat_id)
    if entry is None:
        return False
    ts = time.time() if now is None else now
    if ts >= entry[0]:
        _active.pop(chat_id, None)
        return False
    return True


def status(chat_id: int, now: float | None = None) -> dict[str, Any] | None:
    """Expiry + reason for an active raid, or None."""
    if not is_active(chat_id, now):
        return None
    ts = time.time() if now is None else now
    expires_at, reason = _active[chat_id]
    return {"reason": reason, "expires_in_s": int(max(0, expires_at - ts))}


def release(chat_id: int, *, actor: str = "auto") -> bool:
    """Leave raid state (reversible). Returns True when something changed."""
    if _active.pop(chat_id, None) is None:
        return False
    audit.record(
        audit.build_entry(
            action_id=audit.new_action_id(),
            chat_id=chat_id,
            chat_title="",
            user_id=0,
            username="",
            rule="raid_release",
            action="unlock",
            reason="raid state lifted",
            matched="",
            dry_run=False,
            applied=True,
            actor=actor,
        )
    )
    return True


def reset() -> None:
    _joins.clear()
    _msgs.clear()
    _active.clear()


async def cmd_modraid(update: Any, context: Any) -> None:
    """Admin: /modraid [status|on|off] — inspect or release the lockdown."""
    from rugmunchbot.moderation.commands import _allowed

    chat = update.effective_chat
    if chat is None or not await _allowed(update, context):
        await update.effective_message.reply_text("⛔ Chat admins only.")
        return
    action = ((context.args or ["status"])[0] or "status").lower()
    if action == "on":
        engage(chat.id, "manual lockdown by admin", GuardSettings())
    elif action in {"off", "release"}:
        changed = release(chat.id, actor=f"admin:{update.effective_user.id}")
        await update.effective_message.reply_text(
            "🔓 Raid state released." if changed else "No raid state active."
        )
        return
    current = status(chat.id)
    if current is None:
        await update.effective_message.reply_text("🟢 No raid state active.")
        return
    await update.effective_message.reply_text(
        f"🔴 Raid: {current['reason']} — auto-release in {current['expires_in_s']}s. "
        "/modraid off to release now."
    )


__all__ = [
    "RaidSignal",
    "cmd_modraid",
    "engage",
    "is_active",
    "note_join",
    "note_message",
    "release",
    "reset",
    "status",
]
