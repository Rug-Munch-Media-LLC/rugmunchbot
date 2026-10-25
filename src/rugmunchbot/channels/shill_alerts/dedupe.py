"""Durable alert identity: one scam promoted by ten channels is ONE alert.

Two keys exist on purpose:

* ``observation_key(channel, message_id, entity, day)`` — one *sighting*.
  Replaying the same sighting (restart, re-sweep) appends the same row and
  changes nothing.
* ``alert_key(entity, day)`` — one *alert*. Ten channels collapse into this
  single key and the card lists all ten sources.

The ledger is append-only JSONL: no rewrite race, survives restarts, and a
torn line is skipped rather than fatal.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rugmunchbot.channels.shill_alerts import config

if TYPE_CHECKING:
    import os


def today(now: datetime | None = None) -> str:
    """UTC day bucket (``YYYYMMDD``) — the dedupe horizon."""
    return (now or datetime.now(UTC)).strftime("%Y%m%d")


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:20]


def observation_key(channel: str, message_id: str, entity: str, day: str) -> str:
    """Stable sighting id: channel + message + entity + day."""
    return _digest(channel.lstrip("@").lower(), str(message_id), entity.lower(), day)


def alert_key(entity: str, day: str) -> str:
    """Stable alert id: entity + day. Every channel shares this key."""
    return _digest(entity.lower(), day)


@dataclass(frozen=True, slots=True)
class AlertRecord:
    """What ``record`` writes for one sighting."""

    entity: str
    day: str
    channel: str
    message_id: str
    source: str
    confidence: float
    route: str


class AlertLedger:
    """Append-only ledger; ``record`` says whether the card must be posted."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path or config.ALERT_LEDGER)

    # ── read ──────────────────────────────────────────────────────────────
    def rows(self) -> list[dict[str, object]]:
        """Parsed rows; an unreadable/missing file is an empty ledger."""
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, object]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("k"):
                out.append(row)
        return out

    def sources(self, key: str) -> tuple[str, ...]:
        """Distinct source links recorded for an alert key, in first-seen order."""
        seen: dict[str, None] = {}
        for row in self.rows():
            if row.get("k") == key and row.get("s"):
                seen.setdefault(str(row["s"]), None)
        return tuple(seen)

    def alerted(self, key: str) -> bool:
        """True when this alert key already produced a card (any day-part)."""
        return any(row.get("k") == key and row.get("a") for row in self.rows())

    def suppressed(self, entity: str) -> bool:
        """True when an operator marked this entity a false positive."""
        return any(
            row.get("k") == alert_key(entity, str(row.get("d"))) and row.get("f")
            for row in self.rows()
        )

    # ── write ─────────────────────────────────────────────────────────────
    def _append(self, row: dict[str, object]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError:
            # A full/unwritable volume must not break detection; the alert
            # simply loses its dedupe memory for this run.
            return

    def record(self, rec: AlertRecord, *, post: bool) -> bool:
        """Append one sighting; return True when the caller must send the card.

        ``post=True`` means the routing decision was ``main``. The first such
        record for the day wins; every later sighting of the same entity is
        recorded as a source and returns False — that is the "10 channels, 1
        alert" rule and the restart-replay guarantee in one place.
        """
        key = alert_key(rec.entity, rec.day)
        obs = observation_key(rec.channel, rec.message_id, rec.entity, rec.day)
        first_alert = post and not self.alerted(key)
        self._append(
            {
                "k": key,
                "o": obs,
                "e": rec.entity,
                "d": rec.day,
                "c": rec.channel,
                "m": rec.message_id,
                "s": rec.source,
                "conf": rec.confidence,
                "r": rec.route,
                "a": bool(first_alert),
                "t": datetime.now(UTC).isoformat(),
            }
        )
        return first_alert

    def mark_false_positive(self, entity: str, *, day: str | None = None) -> None:
        """Suppress an entity for the day (operator action, audited by the row)."""
        bucket = day or today()
        self._append({"k": alert_key(entity, bucket), "e": entity, "d": bucket, "f": True})
