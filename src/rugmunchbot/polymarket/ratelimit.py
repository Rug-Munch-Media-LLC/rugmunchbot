"""Minimum-interval limiter, so a cycle can never spam a channel."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class PostRateLimiter:
    """Allow at most one post per ``min_interval_s``."""

    def __init__(self, min_interval_s: float, clock: Callable[[], float] = time.monotonic):
        self._interval = max(min_interval_s, 0.0)
        self._clock = clock
        self._last: float | None = None

    def allow(self) -> bool:
        """True when the interval has elapsed; records the slot when it has."""
        now = self._clock()
        if self._last is not None and now - self._last < self._interval:
            return False
        self._last = now
        return True

    @property
    def remaining(self) -> float:
        if self._last is None:
            return 0.0
        return max(self._interval - (self._clock() - self._last), 0.0)
