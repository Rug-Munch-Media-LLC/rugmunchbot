"""In-process sliding-window state for moderation (single bot process).

Everything here is intentionally memory-only: the bot runs as one process and
a restart simply forgets recent message history. Losing it degrades detection
(dedup/velocity/newness reset), never safety — no rule fires on stale state.

``MAX_KEYS`` bounds memory so an adversarial raid cannot grow the dicts
without limit (oldest key evicted first).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

MAX_KEYS = 20_000
MAX_EVENTS = 64

_by_user: dict[tuple[int, int], deque[float]] = {}
_by_chat: dict[int, deque[float]] = {}
_hashes: dict[tuple[int, int, str], deque[float]] = {}
_first_seen: dict[tuple[int, int], float] = {}
_media: dict[tuple[int, int], deque[float]] = {}


@dataclass(slots=True)
class MessageStats:
    """Counters observed for one message, before/after counting it."""

    user_velocity: int = 0
    chat_velocity: int = 0
    duplicate_count: int = 0
    media_burst: int = 0
    known_since_s: float = 0.0

    @property
    def is_new_member(self) -> bool:
        return self.known_since_s < 600.0


def _prune(store: dict, now: float, window: float) -> None:
    if len(store) <= MAX_KEYS:
        return
    cutoff = now - window
    for key in [k for k, v in store.items() if not v or v[-1] < cutoff]:
        store.pop(key, None)


def _note(store: dict, key: object, now: float, window: float) -> int:
    bucket = store.setdefault(key, deque(maxlen=MAX_EVENTS))
    bucket.append(now)
    while bucket and bucket[0] < now - window:
        bucket.popleft()
    return len(bucket)


def _count_duplicate(chat_id: int, user_id: int, digest: str, now: float, window: float) -> int:
    key = (chat_id, user_id, digest)
    bucket = _hashes.setdefault(key, deque(maxlen=MAX_EVENTS))
    bucket.append(now)
    while bucket and bucket[0] < now - window:
        bucket.popleft()
    return len(bucket)


def note_message(
    chat_id: int,
    user_id: int,
    digest: str,
    *,
    media: bool = False,
    window_s: int = 60,
    dup_window_s: int = 120,
    now: float | None = None,
) -> MessageStats:
    """Record one message and return the counters the rules care about."""
    ts = time.time() if now is None else now
    first = _first_seen.setdefault((chat_id, user_id), ts)
    stats = MessageStats(
        user_velocity=_note(_by_user, (chat_id, user_id), ts, window_s),
        chat_velocity=_note(_by_chat, chat_id, ts, window_s),
        duplicate_count=_count_duplicate(chat_id, user_id, digest, ts, dup_window_s),
        media_burst=_note(_media, (chat_id, user_id), ts, 60.0) if media else 0,
        known_since_s=max(0.0, ts - first),
    )
    _prune(_by_user, ts, 3600)
    _prune(_by_chat, ts, 3600)
    _prune(_hashes, ts, 3600)
    return stats


def mark_member(chat_id: int, user_id: int, now: float | None = None) -> None:
    """Start (or restart) a member's clock — used on join."""
    _first_seen[(chat_id, user_id)] = time.time() if now is None else now


def forget_member(chat_id: int, user_id: int) -> None:
    _first_seen.pop((chat_id, user_id), None)
    _by_user.pop((chat_id, user_id), None)
    _media.pop((chat_id, user_id), None)


def reset() -> None:
    """Wipe all state (tests and /modpardon flows)."""
    for store in (_by_user, _hashes, _first_seen, _media):
        store.clear()
    _by_chat.clear()


__all__ = ["MessageStats", "forget_member", "mark_member", "note_message", "reset"]
