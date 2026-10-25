"""Durable safety state for the moderation layer (atomic JSON snapshot).

Losing *detection* state on a restart is harmless (windows refill). Losing
*containment* state is not: an active raid lockdown would silently lift, a
quarantined member would become unreleasable through the bot, and a newcomer
who never solved the captcha would lose the record that they are still gated.

Only that containment state is written here, in one atomic snapshot:

    raid        -> {chat_id: [expires_epoch, reason]}
    quarantine  -> {chat_id: {user_id: {...pending row...}}}
    gate        -> {"chat:user": {"action_id": ..., "deadline": epoch}}
    state       -> {"verified": {...}, "blocked": [[chat, user], ...]}

Writes are best-effort and never raise: a failed write degrades durability for
new events but cannot block a containment action already in flight. A corrupt
or absent file simply means "nothing to recover" — recovery never invents
state. Path: RUGMUNCH_MODERATION_STATE or <repo>/data/moderation_state.json.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.persist")

PATH = Path(
    os.getenv("RUGMUNCH_MODERATION_STATE")
    or Path(__file__).resolve().parents[3] / "data" / "moderation_state.json"
)
MAX_BYTES = 1_000_000

_cache: dict[str, Any] | None = None


def use_path(path: str | Path) -> None:
    """Point the snapshot at another file (tests, harness) and drop the cache."""
    global PATH, _cache
    PATH = Path(path)
    _cache = None


def load() -> dict[str, Any]:
    """Whole snapshot, cached per process. Corrupt/absent file -> empty dict."""
    global _cache
    if _cache is not None:
        return _cache
    try:
        raw = json.loads(PATH.read_text(encoding="utf-8"))
        _cache = raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        _cache = {}
    return _cache


def save(section: str, payload: Any) -> bool:
    """Merge one section and rewrite the snapshot atomically. Never raises."""
    global _cache
    data = dict(load())
    data[section] = payload
    try:
        blob = json.dumps(data, separators=(",", ":"), default=str)
        if len(blob.encode()) > MAX_BYTES:
            logger.warning("moderation state snapshot too large; not written")
            return False
        PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(PATH.parent), prefix=".modstate-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, PATH)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError:
        logger.warning("moderation state write failed", exc_info=True)
        return False
    _cache = data
    return True


def section(name: str) -> dict[str, Any]:
    """One section of the snapshot, or an empty dict when absent/corrupt."""
    value = load().get(name)
    return value if isinstance(value, dict) else {}


def save_ids(name: str, mapping: dict[int, dict[int, Any]]) -> bool:
    """Persist a chat → user → row mapping (JSON turns the integer keys into text)."""
    return save(
        name,
        {
            str(chat): {str(user): row for user, row in rows.items()}
            for chat, rows in mapping.items()
        },
    )


def restore_ids(name: str) -> dict[int, dict[int, Any]]:
    """Load a chat → user → row section with integer keys; junk is dropped."""
    out: dict[int, dict[int, Any]] = {}
    for chat, rows in section(name).items():
        if not isinstance(rows, dict):
            continue
        try:
            out[int(chat)] = {int(user): row for user, row in rows.items() if isinstance(row, dict)}
        except (TypeError, ValueError):
            continue
    return out


def reset() -> None:
    """Forget the snapshot on disk and in memory (tests, operator reset)."""
    global _cache
    _cache = None
    try:
        PATH.unlink(missing_ok=True)
    except OSError:
        logger.warning("moderation state reset failed", exc_info=True)


__all__ = [
    "MAX_BYTES",
    "PATH",
    "load",
    "reset",
    "restore_ids",
    "save",
    "save_ids",
    "section",
    "use_path",
]
