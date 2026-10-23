"""Bounded audit trail for moderation decisions: ring buffer + JSONL file.

Every row lands in the in-memory ring (newest first, capped) and — when a sink
is attached — in an append-only JSONL file capped at :data:`MAX_PERSISTED`
rows, so ``/modactions`` survives a restart without an unbounded file. Audit
writes are best-effort: a failure is logged and never blocks a decision.
"""

from __future__ import annotations

import json
import threading
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rugmunchbot.support.logging import get_logger

__all__ = ["MAX_AUDIT", "MAX_PERSISTED", "AuditLog", "JsonlAudit", "default_sink"]

logger = get_logger("rmi_bot.community.moderation_audit")

MAX_AUDIT = 200
MAX_PERSISTED = 2000
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "moderation" / "audit.jsonl"


class JsonlAudit:
    """Append-only, row-capped JSONL trail shared by every chat."""

    def __init__(self, path: Path | str | None = None, max_rows: int = MAX_PERSISTED) -> None:
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self.max_rows = max(1, int(max_rows))
        self._lock = threading.Lock()
        self._rows = -1  # unknown until the first append counts the file

    def append(self, row: Mapping[str, Any]) -> None:
        """Append one row, trimming the file once it exceeds :attr:`max_rows`."""
        line = json.dumps(dict(row), separators=(",", ":"), sort_keys=True, default=str)
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self._rows < 0:
                    self._rows = self._count()
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                self._rows += 1
                if self._rows > self.max_rows:
                    self._trim()
            except OSError:
                logger.warning("moderation_audit_append_failed path=%s", self.path, exc_info=True)

    def read(self, chat_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Return the newest *limit* rows (optionally one chat), newest first."""
        limit = max(1, int(limit))
        rows: list[dict[str, Any]] = []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    try:
                        row = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(row, dict) and (chat_id is None or row.get("chat_id") == int(chat_id)):
                        rows.append(row)
        except OSError:
            return []
        return rows[-limit:][::-1]

    def _count(self) -> int:
        try:
            with self.path.open("rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0

    def _trim(self) -> None:
        """Rewrite the file keeping the newest ``max_rows`` rows."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        kept = lines[-self.max_rows :]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        self._rows = len(kept)


class AuditLog:
    """In-memory audit ring — one row per decision or manual action."""

    def __init__(self, maxlen: int = MAX_AUDIT, sink: JsonlAudit | None = None) -> None:
        self._rows: deque[dict[str, Any]] = deque(maxlen=int(maxlen))
        self._sink = sink

    def record(self, row: dict[str, Any]) -> None:
        """Keep *row* in memory and hand it to the sink when one is attached."""
        self._rows.appendleft(row)
        if self._sink is None:
            return
        try:
            self._sink.append(row)
        except Exception:  # an audit sink must never break a decision
            logger.warning("moderation_audit_sink_failed", exc_info=True)

    def actions(self, chat_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Newest-first rows, falling back to the sink when memory has none."""
        limit = max(1, int(limit))
        rows = [r for r in self._rows if chat_id is None or r["chat_id"] == int(chat_id)]
        if rows or self._sink is None:
            return rows[:limit]
        return self._sink.read(chat_id, limit)

    def __len__(self) -> int:
        return len(self._rows)


def default_sink() -> JsonlAudit:
    """The process-wide JSONL sink the live bot attaches to its engine."""
    return JsonlAudit()
