"""Bounded, newest-first audit trail for moderation decisions."""

from __future__ import annotations

from collections import deque
from typing import Any

__all__ = ["MAX_AUDIT", "AuditLog"]

MAX_AUDIT = 200


class AuditLog:
    """In-memory audit ring — one row per decision or manual action."""

    def __init__(self, maxlen: int = MAX_AUDIT) -> None:
        self._rows: deque[dict[str, Any]] = deque(maxlen=int(maxlen))

    def record(self, row: dict[str, Any]) -> None:
        self._rows.appendleft(row)

    def actions(self, chat_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        rows = [r for r in self._rows if chat_id is None or r["chat_id"] == int(chat_id)]
        return rows[: max(1, int(limit))]

    def __len__(self) -> int:
        return len(self._rows)
