"""Append-only audit trail for every moderation decision (JSONL).

One record per decision — including dry-run and "allowed" no-ops are NOT
recorded (volume), only actions. Writes are best-effort: an audit failure is
logged and never blocks moderation.

Path: ``RUGMUNCH_MODERATION_AUDIT`` or ``<repo>/data/moderation_actions.jsonl``.
Rotate with logrotate; each line is self-contained JSON.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.audit")

AUDIT_PATH = Path(
    os.getenv("RUGMUNCH_MODERATION_AUDIT")
    or Path(__file__).resolve().parents[3] / "data" / "moderation_actions.jsonl"
)
MAX_PREVIEW = 140


def new_action_id() -> str:
    """Short, sortable id an admin can quote (also the appeal handle)."""
    return f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


def build_entry(
    *,
    action_id: str,
    chat_id: int,
    chat_title: str,
    user_id: int,
    username: str,
    rule: str,
    action: str,
    reason: str,
    matched: str,
    dry_run: bool,
    applied: bool,
    error: str | None = None,
    text: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalise one audit record. Never includes tokens or raw secrets."""
    return {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "epoch": int(time.time()),
        "action_id": action_id,
        "chat_id": chat_id,
        "chat_title": chat_title,
        "user_id": user_id,
        "username": username,
        "rule": rule,
        "action": action,
        "reason": reason,
        "matched": matched[:200],
        "text_preview": (text or "").replace("\n", " ")[:MAX_PREVIEW],
        "dry_run": dry_run,
        "applied": applied,
        "error": error,
        **(extra or {}),
    }


def record(entry: dict[str, Any]) -> None:
    """Append one record. Best-effort: failures are logged, never raised."""
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        logger.warning("moderation audit write failed", exc_info=True)


def tail(chat_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Return the last *limit* records, newest first, optionally per chat."""
    if not AUDIT_PATH.exists():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(limit * 20, 200))
    try:
        with open(AUDIT_PATH, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if chat_id is None or row.get("chat_id") == chat_id:
                    rows.append(row)
    except OSError:
        return []
    return list(reversed(rows))[:limit]


__all__ = ["AUDIT_PATH", "build_entry", "new_action_id", "record", "tail"]
