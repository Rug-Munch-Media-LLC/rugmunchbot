"""Raid / flood detection: two sliding windows, one reversible lockdown.

Crossing either window (raid_joins inside raid_window_s, flood_msgs inside
flood_window_s) engages the containment state in raid_state.py, which is
audited and durable. Verified and trusted members keep talking during a raid,
so a raid never silences the room it protects. Dry-run chats audit the
detection and engage NOTHING - a rehearsal must never mute a live room.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from rugmunchbot.moderation import raid_state
from rugmunchbot.moderation.guard_settings import GuardSettings
from rugmunchbot.moderation.raid_state import engage, is_active, recover, release, status

MAX_CHATS = 5_000
MAX_EVENTS = 256
_joins: dict[int, deque[float]] = {}
_msgs: dict[int, deque[float]] = {}
_dry_until: dict[int, float] = {}


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


def note_join(
    chat_id: int, settings: GuardSettings, now: float | None = None, *, dry_run: bool = False
) -> RaidSignal:
    """Count a join; engage when raid_joins land inside raid_window_s."""
    ts = time.time() if now is None else now
    count = _note(_joins, chat_id, settings.raid_window_s, ts)
    return _judge(
        chat_id, count, settings.raid_joins, settings.raid_window_s, settings, ts, dry_run, "joins"
    )


def note_message(
    chat_id: int, settings: GuardSettings, now: float | None = None, *, dry_run: bool = False
) -> RaidSignal:
    """Count a message; engage when flood_msgs land inside flood_window_s."""
    ts = time.time() if now is None else now
    count = _note(_msgs, chat_id, settings.flood_window_s, ts)
    return _judge(
        chat_id,
        count,
        settings.flood_msgs,
        settings.flood_window_s,
        settings,
        ts,
        dry_run,
        "messages",
    )


def _judge(
    chat_id: int,
    count: int,
    threshold: int,
    window_s: int,
    settings: GuardSettings,
    ts: float,
    dry_run: bool,
    unit: str,
) -> RaidSignal:
    """Shared threshold logic for both windows (one boundary, one code path)."""
    if count < threshold:
        return RaidSignal(count, window_s, False)
    if raid_state.is_active(chat_id, ts):
        reason = (raid_state.status(chat_id, ts) or {}).get("reason", "raid active")
        return RaidSignal(count, window_s, True, reason)
    reason = f"{count} {unit} within {window_s}s"
    if dry_run:
        if ts < _dry_until.get(chat_id, 0.0):
            return RaidSignal(count, window_s, True, reason)
        _dry_until[chat_id] = ts + settings.raid_hold_s
        engage(chat_id, reason, settings, ts, dry_run=True)
        return RaidSignal(count, window_s, True, reason)
    engage(chat_id, reason, settings, ts)
    return RaidSignal(count, window_s, True, reason)

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


def reset() -> None:
    _joins.clear()
    _msgs.clear()
    _dry_until.clear()
    raid_state.reset()


__all__ = [
    "RaidSignal",
    "cmd_modraid",
    "engage",
    "is_active",
    "note_join",
    "note_message",
    "recover",
    "release",
    "reset",
    "status",
]
