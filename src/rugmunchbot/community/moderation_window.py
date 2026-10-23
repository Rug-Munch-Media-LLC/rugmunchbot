"""Per-user duplicate-message window used by the moderation engine."""

from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot.community.moderation_models import MessageEvent, Policy

__all__ = ["DuplicateWindow"]

MAX_SEEN = 50


class DuplicateWindow:
    """Counts normalized repeats of one user's text inside a sliding window."""

    def __init__(self, clock: Any) -> None:
        self._clock = clock
        self._seen: dict[tuple[int, int], deque[tuple[float, str]]] = {}

    def hit(self, ev: MessageEvent, policy: Policy) -> bool:
        """Record *ev* and report whether it crossed the duplicate threshold."""
        key = (int(ev.chat_id), int(ev.user_id))
        window = self._seen.setdefault(key, deque(maxlen=MAX_SEEN))
        now = self._clock()
        while window and now - window[0][0] > policy.duplicate_window_s:
            window.popleft()
        normalized = re.sub(r"\W+", "", (ev.text or "").lower())[:200]
        if not normalized:
            return False
        hits = 1 + sum(1 for _, seen in window if seen == normalized)
        window.append((now, normalized))
        return hits >= policy.duplicate_threshold
