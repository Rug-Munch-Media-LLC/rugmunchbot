"""Durable containment state for raid lockdown (engage / release / recover).

Split from raidmode.py so the detector and the state machine each stay small.
The single source of truth for an active hold is this dict plus the atomic
snapshot on disk: a restart restores the hold instead of silently lifting it.
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot.moderation import audit, persist
from rugmunchbot.moderation.guard_settings import GuardSettings
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.raid_state")

SECTION = "raid"
_active: dict[int, tuple[float, str]] = {}


def _sync() -> None:
    persist.save(SECTION, {str(k): [v[0], v[1]] for k, v in _active.items()})


def recover() -> int:
    """Restore unexpired lockdowns from the snapshot. Returns how many."""
    _active.clear()
    raw = persist.load().get(SECTION) or {}
    now = time.time()
    for key, value in raw.items():
        try:
            expires_at, reason = float(value[0]), str(value[1])
            chat_id = int(key)
        except (IndexError, TypeError, ValueError):
            continue
        if expires_at > now:
            _active[chat_id] = (expires_at, reason)
    if _active:
        logger.warning("raid state recovered after restart", extra={"chats": len(_active)})
    return len(_active)


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
    if not dry_run:
        _active[chat_id] = (ts + settings.raid_hold_s, reason)
        _sync()
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
    entry["audit_written"] = audit.record(entry)
    logger.warning("raid state engaged", extra={"chat_id": chat_id, "reason": reason})
    return entry


def is_active(chat_id: int, now: float | None = None) -> bool:
    """True while the raid hold has not expired."""
    entry = _active.get(chat_id)
    if entry is None:
        return False
    ts = time.time() if now is None else now
    if ts >= entry[0]:
        _active.pop(chat_id, None)
        _sync()
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
    _sync()
    entry = audit.build_entry(
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
    entry["audit_written"] = audit.record(entry)
    return True


def reset() -> None:
    _active.clear()


__all__ = ["engage", "is_active", "recover", "release", "reset", "status"]
