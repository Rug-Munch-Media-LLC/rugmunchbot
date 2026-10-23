"""Per-chat community settings: opt-in only, safe defaults.

The block lives beside the moderation policy in ``configs/moderation.json``::

    {"chats": {"-100123": {"dry_run": true,
                           "community": {"welcome_enabled": true,
                                         "welcome_text": "Hi {name}!"}}}}

Every switch is OFF by default, so adding a chat to the moderation config never
starts greeting, auto-replying, banning or scheduling anything until an operator
says so. Punitive behaviour (``enforce_gban``) additionally needs a live chat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugmunchbot.moderation import config, store

MAX_TEXT = 600

DEFAULTS: dict[str, Any] = {
    "welcome_enabled": False,
    "goodbye_enabled": False,
    "welcome_text": "",
    "goodbye_text": "",
    "filters_enabled": False,
    "filter_replies_per_min": 3,
    "enforce_gban": False,
    "scheduled_enabled": False,
    "schedule_max": 10,
    "notes_admins_only": True,
}

_BOOLS = tuple(k for k, v in DEFAULTS.items() if isinstance(v, bool))
_TEXTS = ("welcome_text", "goodbye_text")


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return min(high, max(low, int(value)))
    except (TypeError, ValueError):
        return fallback


@dataclass(slots=True)
class CommunityConfig:
    """Effective community settings for one chat."""

    chat_id: int
    welcome_enabled: bool = False
    goodbye_enabled: bool = False
    welcome_text: str = ""
    goodbye_text: str = ""
    filters_enabled: bool = False
    filter_replies_per_min: int = 3
    enforce_gban: bool = False
    scheduled_enabled: bool = False
    schedule_max: int = 10
    notes_admins_only: bool = True

    @classmethod
    def from_dict(cls, chat_id: int, block: Any) -> CommunityConfig:
        """Merge a chat block's ``community`` section over the safe defaults."""
        raw = block.get("community") if isinstance(block, dict) else None
        merged: dict[str, Any] = dict(DEFAULTS)
        if isinstance(raw, dict):
            merged.update({k: v for k, v in raw.items() if k in DEFAULTS})
        for key in _BOOLS:
            merged[key] = merged[key] is True
        for key in _TEXTS:
            merged[key] = str(merged[key] or "")[:MAX_TEXT]
        merged["filter_replies_per_min"] = _clamp(merged.get("filter_replies_per_min"), 1, 10, 3)
        merged["schedule_max"] = _clamp(merged.get("schedule_max"), 1, 50, 10)
        return cls(chat_id=int(chat_id), **merged)

    def any_enabled(self) -> bool:
        return any(
            (
                self.welcome_enabled,
                self.goodbye_enabled,
                self.filters_enabled,
                self.scheduled_enabled,
            )
        )


def load_community_config(chat_id: int) -> CommunityConfig:
    """Config values only (JSON file, hot-reloaded). Never touches the store."""
    chats = config.raw_config().get("chats") or {}
    return CommunityConfig.from_dict(chat_id, chats.get(str(chat_id)))


async def effective(chat_id: int) -> CommunityConfig:
    """Config values with the store's admin overrides applied on top."""
    cfg = load_community_config(chat_id)
    overrides = await store.all_fields(store.chat_key(chat_id, "settings"))
    for key in _TEXTS:
        if overrides.get(key):
            setattr(cfg, key, overrides[key][:MAX_TEXT])
    return cfg


__all__ = ["DEFAULTS", "CommunityConfig", "effective", "load_community_config"]
