"""Duplicate suppression and the per-minute card cap.

One process-local ledger. The clock is injected so tests drive the windows
without sleeping. Nothing here raises: a duplicate or an over-cap chat is a
dropped card plus a counter, never an exception inside the poller.
"""

from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["CardLedger"]

WINDOW_S = 60.0
MAX_KEYS = 4096


class CardLedger:
    """Dedupe claims and per-chat card budgets, keyed by short strings."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._cards: dict[int, deque[float]] = {}
        self._drops: dict[int, int] = {}

    def claim(self, key: str, window_s: int) -> bool:
        """``True`` the first time *key* is seen inside *window_s*, else ``False``."""
        now = self._clock()
        if len(self._seen) > MAX_KEYS:
            self._prune(now, max(window_s, 3600))
        last = self._seen.get(key)
        if last is not None and now - last < window_s:
            return False
        self._seen[key] = now
        return True

    def allow_card(self, chat_id: int, max_per_min: int) -> bool:
        """``True`` when the chat has posted fewer than *max_per_min* cards this minute."""
        now = self._clock()
        budget = self._cards.setdefault(int(chat_id), deque())
        while budget and now - budget[0] >= WINDOW_S:
            budget.popleft()
        if len(budget) >= max(1, int(max_per_min)):
            self._drops[int(chat_id)] = self._drops.get(int(chat_id), 0) + 1
            return False
        budget.append(now)
        return True

    def drops(self, chat_id: int) -> int:
        """Cards dropped by the rate cap for *chat_id* since process start."""
        return self._drops.get(int(chat_id), 0)

    def forget(self, chat_id: int) -> None:
        """Drop the rate-cap window and drop counter for *chat_id* (keeps claims)."""
        self._cards.pop(int(chat_id), None)
        self._drops.pop(int(chat_id), None)

    def _prune(self, now: float, window_s: float) -> None:
        stale = [key for key, stamp in self._seen.items() if now - stamp >= window_s]
        for key in stale:
            self._seen.pop(key, None)
