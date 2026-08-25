"""Main-channel daily cap — HARD ≤9 posts / 24h sliding window (WO-2).

Implements the cap law from /root/.knowledge/CONTENT-STRATEGY-CANONICAL.md:
"@cryptorugmuncher — <=9/day HARD CAP". Every additive main-channel post
(escalations from alerts/scans/alpha jobs, high-importance news stories)
must pass :meth:`MainChannelCap.admission` BEFORE sending and call
:meth:`MainChannelCap.record` AFTER a successful send, so failed sends never
consume budget.

Durability: a Redis sorted set (member=unique id, score=post epoch) gives a
cross-restart accurate window; an in-process deque mirrors every record so
the cap keeps enforcing even while Redis is down (fail-open across restarts
only — never wide open in-process). Pruning drops entries older than the
window on every read.

This module is deliberately separate from ``router.py``: the router stays a
pure decision function; the cap owns state.
"""

from __future__ import annotations

import os
import time
import uuid
from collections import deque
from typing import Any

from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)

#: HARD cap: max main-channel posts in any 24h window.
MAIN_DAILY_CAP = int(os.getenv("RMI_MAIN_DAILY_CAP", "9"))

#: Sliding window length in seconds.
CAP_WINDOW_SECONDS = int(os.getenv("RMI_MAIN_CAP_WINDOW", "86400"))

#: Redis key for the durable window (sorted set, score = post epoch).
_REDIS_KEY = "rmi:channels:main_posts"

__all__ = [
    "CAP_WINDOW_SECONDS",
    "MAIN_DAILY_CAP",
    "MainChannelCap",
]


class MainChannelCap:
    """24h sliding-window counter with Redis durability + local mirror."""

    def __init__(self, cap: int = MAIN_DAILY_CAP) -> None:
        self._cap = max(1, cap)
        self._local: deque[float] = deque()
        self._redis: Any | None = None
        self._redis_down = False

    # ── internals ────────────────────────────────────────────────────

    def _prune_local(self, now: float) -> None:
        horizon = now - CAP_WINDOW_SECONDS
        while self._local and self._local[0] <= horizon:
            self._local.popleft()

    def _acquire(self) -> Any | None:
        if self._redis_down:
            return None
        if self._redis is None:
            try:
                self._redis = get_redis_async()
            except Exception:  # fail-open to local-only enforcement
                self._redis = None
            if self._redis is None:
                self._redis_down = True
        return self._redis

    def _mark_down(self) -> None:
        self._redis = None
        self._redis_down = True

    # ── public API ───────────────────────────────────────────────────

    async def count(self) -> int:
        """Posts recorded inside the window. Redis authoritative when up."""
        now = time.time()
        self._prune_local(now)
        r = self._acquire()
        if r is None:
            return len(self._local)
        try:
            await r.zremrangebyscore(_REDIS_KEY, "-inf", now - CAP_WINDOW_SECONDS)
            n = await r.zcard(_REDIS_KEY)
            return int(n)
        except Exception as exc:
            logger.debug("main_cap_redis_down err=%s", str(exc)[:120])
            self._mark_down()
            return len(self._local)

    async def admission(self) -> tuple[bool, int]:
        """``(allowed, current_count)`` — call BEFORE sending to main."""
        n = await self.count()
        return n < self._cap, n

    async def record(self) -> None:
        """Count one successful main-channel post (call AFTER send ok)."""
        now = time.time()
        self._local.append(now)
        r = self._acquire()
        if r is None:
            return
        try:
            member = f"{now:.6f}:{uuid.uuid4().hex[:12]}"
            await r.zadd(_REDIS_KEY, {member: now})
            await r.expire(_REDIS_KEY, CAP_WINDOW_SECONDS + 60)
        except Exception as exc:
            logger.debug("main_cap_record_redis_failed err=%s", str(exc)[:120])
            self._mark_down()

    @property
    def cap(self) -> int:
        return self._cap


#: Process-wide cap instance (one bot process = one main channel).
main_cap = MainChannelCap()
