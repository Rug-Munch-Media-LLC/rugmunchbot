"""ct_shill_watch — env-overridable config, data paths, clock helper.

Split out of ``channels/ct_shill_watch.py`` (2026-10-25). Every knob lives
here so callers/tests resolve it in exactly one place.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

CT_SHILL_ENABLED = os.getenv("RMI_CT_SHILL_ENABLED", "true").lower() in ("1", "true", "yes")
#: Tick interval (minutes). The operator asked for 15.
CT_SHILL_INTERVAL_MIN = int(os.getenv("RMI_CT_SHILL_INTERVAL_MIN", "15"))
#: Accounts pulled per tick (retired — X ingestion is owned by rugbrain).
CT_SHILL_ACCOUNTS_PER_TICK = int(os.getenv("RMI_CT_SHILL_ACCOUNTS_PER_TICK", "2"))
#: Tweets inspected per account timeline.
CT_SHILL_TWEETS_PER_ACCOUNT = int(os.getenv("RMI_CT_SHILL_TWEETS_PER_ACCOUNT", "10"))
#: Engagement floor (likes + retweets) for a promo tweet to count as reach.
CT_SHILL_MIN_ENGAGEMENT = int(os.getenv("RMI_CT_SHILL_MIN_ENGAGEMENT", "25"))
#: Max screenshots captured per tick (one DeepInfra vision call each).
CT_SHILL_SCREENSHOT_BUDGET = int(os.getenv("RMI_CT_SHILL_SCREENSHOT_BUDGET", "3"))
#: Max wall-of-shame writes per tick (bounded, no list spam).
CT_SHILL_MAX_ADDS_PER_TICK = int(os.getenv("RMI_CT_SHILL_MAX_ADDS", "3"))

#: Daily refresh knobs.
KOL_DAILY_REFRESH_ENABLED = os.getenv("RMI_KOL_DAILY_REFRESH_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
KOL_DAILY_INTERVAL_MIN = int(os.getenv("RMI_KOL_DAILY_INTERVAL_MIN", "1440"))
#: Retweeter-discovery pulls per daily run (retired — X owned by rugbrain).
KOL_DAILY_MAX_RETWEETER_PULLS = int(os.getenv("RMI_KOL_DAILY_MAX_RETWEETER_PULLS", "1"))

#: Canonical Qdrant endpoint + social-intel collection for shill records
#: (same direct-upsert pattern as ``shill_watch`` → ``threat_intel_v2``).
QDRANT_URL = os.getenv("RMI_QDRANT_URL") or os.getenv(
    "QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://rmi-qdrant-v2:6333")
).rstrip("/")
SOCIAL_INTEL_COLLECTION = os.getenv("RMI_SOCIAL_INTEL_COLLECTION", "social_intel")

#: Data paths (``channels/ct_shill_watch`` -> ``rugmunchbot/data/kol``).
_HERE = os.path.dirname(os.path.abspath(__file__))
_KOL_DIR = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "kol"))
INFLUENTIAL_ACCOUNTS_PATH = os.getenv(
    "RMI_INFLUENTIAL_ACCOUNTS_PATH", os.path.join(_KOL_DIR, "influential_accounts.json")
)
WALL_OF_SHAME_PATH = os.getenv(
    "RMI_WALL_OF_SHAME_PATH", os.path.join(_KOL_DIR, "wall_of_shame.json")
)
#: Rotation cursor so each 15-min tick pulls the NEXT slice of the CT
#: registry (no account is starved; the whole registry cycles).
CURSOR_PATH = os.getenv("RMI_CT_SHILL_CURSOR_PATH", os.path.join(_KOL_DIR, "ct_cursor.json"))

_BLUECHIP = {"BTC", "ETH", "SOL", "USDT", "USDC", "BNB"}


def now_iso() -> str:
    """UTC ISO-8601 timestamp (single clock for every wall write)."""
    return datetime.now(UTC).isoformat()
