"""Process-local dedupe with a TTL. One key = one published card."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_TTL_S = 21_600.0


class Dedupe:
    """Remember keys for ``ttl_s``. ``claim`` is atomic within one process."""

    def __init__(self, ttl_s: float = DEFAULT_TTL_S, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_s
        self._clock = clock
        self._seen: dict[str, float] = {}

    def _evict(self, now: float) -> None:
        for key, expiry in list(self._seen.items()):
            if expiry <= now:
                del self._seen[key]

    def claim(self, key: str) -> bool:
        """True when the key is new (and now marked); False when already seen."""
        now = self._clock()
        self._evict(now)
        if key in self._seen:
            return False
        self._seen[key] = now + self._ttl
        return True

    @property
    def size(self) -> int:
        return len(self._seen)
