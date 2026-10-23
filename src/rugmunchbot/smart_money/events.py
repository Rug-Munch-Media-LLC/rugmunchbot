"""Event-level dedupe + per-channel post rate limit.

Channel posts are event-driven: the same wallet move, funding squeeze or
market must never post twice, and a burst must never exceed the fleet alert
budget (5 posts/hour per channel).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class EventGate:
    """Dedupe by event key, then enforce a sliding one-hour post budget."""

    def __init__(
        self,
        *,
        ttl: float = 21_600.0,
        max_per_hour: int = 5,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl = ttl
        self.max_per_hour = max_per_hour
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._sent: list[float] = []

    def allow(self, key: str) -> str:
        """Return ``ok`` (recorded), ``dupe`` (seen within TTL) or ``rate``."""
        now = self._clock()
        self._prune(now)
        if key in self._seen:
            return "dupe"
        if len(self._sent) >= self.max_per_hour:
            return "rate"
        self._seen[key] = now
        self._sent.append(now)
        return "ok"

    def _prune(self, now: float) -> None:
        expired = [k for k, ts in self._seen.items() if now - ts > self.ttl]
        for key in expired:
            self._seen.pop(key, None)
        self._sent = [ts for ts in self._sent if now - ts <= 3600.0]


def event_key(feed_kind: str, item: dict) -> str:
    """Stable identity for one event — never includes a timestamp."""
    ident = (
        item.get("tx_hash")
        or item.get("market_id")
        or f"{item.get('asset') or item.get('symbol')}:{item.get('kind') or item.get('action')}"
    )
    return f"{feed_kind}:{ident}"
