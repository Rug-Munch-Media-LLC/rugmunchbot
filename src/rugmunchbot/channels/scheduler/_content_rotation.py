"""Content rotation scheduler — 8 high-quality posts per day.

Every post must pass importance reranking before publication.
Only reranked-to-main content gets pushed to feed.rugmunch.io.
Quality > quantity: if fewer than 8 stories pass the bar, we post fewer.

Content calendar (UTC):
  06:00 — The Morning Rug (full market brief + AI analysis)
  08:00 — Whale Watch (top HL + chain movements)
  10:00 — Security Spotlight (tokens matching rug DNA)
  12:00 — Midday Market Update (condition + funding dislocations)
  14:00 — Cascade Alert (liquidation proximity warnings)
  16:00 — Smart Money Report (convergence + accumulation patterns)
  19:00 — Scam Exposure (new rugs, honeypots, wash tokens found)
  21:00 — Evening Wrap (day summary + what to watch tomorrow)

Each post includes:
  - Rug Munch Intelligence byline
  - Data provenance (which sources informed the analysis)
  - @rugmunchbot shill + rugmunch.io link
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

CONTENT_SLOTS = [
    {
        "time_utc": "06:00",
        "type": "morning_market_report",
        "label": "The Morning Rug",
        "priority": "high",
    },
    {"time_utc": "08:00", "type": "whale_watch", "label": "Whale Watch", "priority": "medium"},
    {
        "time_utc": "10:00",
        "type": "security_spotlight",
        "label": "Security Spotlight",
        "priority": "high",
    },
    {
        "time_utc": "12:00",
        "type": "market_pulse",
        "label": "Midday Market Update",
        "priority": "medium",
    },
    {"time_utc": "14:00", "type": "cascade_alert", "label": "Cascade Alert", "priority": "high"},
    {
        "time_utc": "16:00",
        "type": "smart_money_report",
        "label": "Smart Money Report",
        "priority": "medium",
    },
    {"time_utc": "19:00", "type": "scam_exposure", "label": "Scam Exposure", "priority": "high"},
    {"time_utc": "21:00", "type": "evening_wrap", "label": "Evening Wrap", "priority": "medium"},
]

_posted_slots: set[str] = set()


def get_pending_slot(utc_hour: int) -> dict[str, Any] | None:
    for slot in CONTENT_SLOTS:
        hour = int(slot["time_utc"].split(":")[0])
        if hour <= utc_hour and slot["type"] not in _posted_slots:
            return slot
    return None


def mark_posted(slot_type: str) -> None:
    _posted_slots.add(slot_type)


def reset_daily() -> None:
    _posted_slots.clear()
