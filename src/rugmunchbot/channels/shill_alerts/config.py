"""Env knobs for the shill/promo alert lane (constants only — no I/O)."""

from __future__ import annotations

import os
import tempfile

#: Master switch. Off = the runner returns an empty report and sends nothing.
ENABLED = os.getenv("RMI_SHILL_ALERTS_ENABLED", "true").lower() in ("1", "true", "yes")

#: Channel role the card is delivered to (resolved by ``scheduler._core._chat_id``).
CHANNEL_ROLE = os.getenv("RMI_SHILL_ALERTS_CHANNEL_ROLE", "alerts")

#: Durable, append-only ledgers (restart replay is a file read, not memory).
_LEDGER_DIR = os.getenv("RMI_SHILL_ALERTS_DIR", "/var/lib/rugmunchbot")
ALERT_LEDGER = os.getenv(
    "RMI_SHILL_ALERTS_LEDGER", os.path.join(_LEDGER_DIR, "shill_alerted.jsonl")
)
REVIEW_LEDGER = os.getenv(
    "RMI_SHILL_ALERTS_REVIEW_LEDGER", os.path.join(_LEDGER_DIR, "shill_alert_review.jsonl")
)

#: Publication limits. One card per entity per UTC day, N cards per tick.
MAX_PER_RUN = int(os.getenv("RMI_SHILL_ALERTS_MAX_PER_RUN", "3"))
MIN_INTERVAL_S = int(os.getenv("RMI_SHILL_ALERTS_MIN_INTERVAL_S", "30"))

#: Quiet hours in UTC, ``"22-06"`` (wraps midnight). Empty = always allowed.
QUIET_HOURS = os.getenv("RMI_SHILL_ALERTS_QUIET_HOURS", "22-06").strip()

#: Confidence floor for the main channel (two-signal gate still applies).
MIN_CONFIDENCE = float(os.getenv("RMI_SHILL_ALERTS_MIN_CONFIDENCE", "0.5"))

#: A controlled trial directory when the deploy path is not writable (tests/dev).
FALLBACK_DIR = os.path.join(tempfile.gettempdir(), "rmi", "shill_alerts")
