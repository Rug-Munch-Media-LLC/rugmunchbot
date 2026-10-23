"""Load per-chat policy from JSON, hot-reload on mtime, fail safe.

Shipped state is inert: ``configs/moderation.json`` has an empty ``chats`` map,
so no chat is guarded until an operator adds its id.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from rugmunchbot.moderation.policy import DEFAULTS, ChatPolicy
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.config")

CONFIG_PATH = Path(
    os.getenv("RUGMUNCH_MODERATION_CONFIG")
    or Path(__file__).resolve().parents[3] / "configs" / "moderation.json"
)

_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _raw_config() -> dict[str, Any]:
    """Read+parse the JSON config, memoised on (path, mtime). Fail-open."""
    key = str(CONFIG_PATH)
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return {"chats": {}}
    cached = _cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("moderation config must be a JSON object")
    except (OSError, ValueError):
        logger.warning("moderation config unreadable, moderation disabled", exc_info=True)
        return {"chats": {}}
    _cache[key] = (mtime, data)
    return data


def raw_config() -> dict[str, Any]:
    """Public read of the hot-reloaded document (community settings, overview)."""
    return _raw_config()


def use_config_path(path: str | Path) -> None:
    """Point the loader at another file (harness, tests). Clears the cache."""
    global CONFIG_PATH
    CONFIG_PATH = Path(path)
    _cache.clear()


def load_chat_policy(chat_id: int) -> ChatPolicy | None:
    """Return the effective policy for *chat_id*, or None when unguarded.

    None means "not configured or disabled" — the caller must do nothing.
    """
    raw_chats = _raw_config().get("chats") or {}
    block = raw_chats.get(str(chat_id))
    if not isinstance(block, dict):
        return None
    policy = ChatPolicy.from_dict(int(chat_id), block)
    return policy if policy.enabled else None


def guarded_chat_ids() -> list[int]:
    """Ids of every chat present in the config (enabled or not)."""
    chats = _raw_config().get("chats") or {}
    return [int(cid) for cid in chats if str(cid).lstrip("-").isdigit()]


__all__ = [
    "CONFIG_PATH",
    "DEFAULTS",
    "ChatPolicy",
    "guarded_chat_ids",
    "load_chat_policy",
    "raw_config",
    "use_config_path",
]
