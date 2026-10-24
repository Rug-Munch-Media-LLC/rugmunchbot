"""Tunable guard thresholds — env first, per-chat override second.

Nothing in the entrance/raid/quarantine rules hardcodes a number: every
threshold, window and action is a field here. Resolution order per chat:

  1. GuardSettings defaults (safe).
  2. RUGMUNCH_MOD_<FIELD> environment overrides (fleet-wide operator knob).
  3. a "guard" object inside the chat's block in configs/moderation.json.

The chat-level block wins because it is the narrowest scope. Unknown keys are
dropped, invalid values fall back to the previous layer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace
from typing import Any

from rugmunchbot.moderation.config import raw_config

PREFIX = "RUGMUNCH_MOD_"


@dataclass(slots=True, frozen=True)
class GuardSettings:
    """Every knob the channel guard exposes (all overridable)."""

    bot_quarantine: bool = True
    new_account_id_min: int = 7_000_000_000  # ~2023 watermark on Telegram ids
    score_new_account: int = 35
    score_no_username: int = 15
    score_bot_pattern: int = 30
    score_scam_name: int = 30
    score_empty_name: int = 10
    quarantine_score: int = 50
    raid_joins: int = 8
    raid_window_s: int = 30
    raid_hold_s: int = 600
    raid_message_action: str = "delete"
    flood_msgs: int = 25
    flood_window_s: int = 10
    quarantine_channel_id: int = 0
    quarantine_mute_minutes: int = 1440
    trusted_user_ids: tuple[int, ...] = ()


_BOOLS = {"bot_quarantine"}
_TUPLES = {"trusted_user_ids"}
_ACTIONS = {"raid_message_action": {"warn", "delete", "mute", "alert"}}


def _coerce(name: str, value: Any, fallback: Any) -> Any:
    """Convert one raw value to the field's type, or return *fallback*."""
    if name in _TUPLES:
        try:
            return tuple(int(v) for v in (value or ()))
        except (TypeError, ValueError):
            return fallback
    if name in _BOOLS:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _merge(base: GuardSettings, raw: dict[str, Any]) -> GuardSettings:
    known = {f.name: getattr(base, f.name) for f in fields(base)}
    for name, value in (raw or {}).items():
        if name not in known or value is None or value == "":
            continue
        if name in _ACTIONS and str(value).lower() not in _ACTIONS[name]:
            continue
        known[name] = _coerce(name, value, known[name])
    return replace(base, **known)


def from_env(env: dict[str, str] | None = None) -> GuardSettings:
    """Defaults + env overrides (RUGMUNCH_MOD_<FIELD>, upper-case)."""
    source = os.environ if env is None else env
    raw: dict[str, Any] = {}
    for field in fields(GuardSettings):
        key = PREFIX + field.name.upper()
        if key in source:
            raw[field.name] = source[key]
    return _merge(GuardSettings(), raw)


def for_chat(chat_id: int) -> GuardSettings:
    """Env-resolved settings plus the chat's "guard" block (if any)."""
    chats = raw_config().get("chats") or {}
    block = chats.get(str(chat_id)) if isinstance(chats, dict) else None
    guard = block.get("guard") if isinstance(block, dict) else None
    return _merge(from_env(), guard if isinstance(guard, dict) else {})


__all__ = ["PREFIX", "GuardSettings", "for_chat", "from_env"]
