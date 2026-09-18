"""Trending topics + mention monitoring channel — RETIRED (X owned by rugbrain).

X ingestion (fetch, normalize, embed, ingest) is owned by **rugbrain** (the
RAG service). The bot no longer runs GetXAPI trends/mentions/status calls;
X-derived intel is consumed through the RAG HTTP API (``tools/rag_lookup.py``).

This module is retained as a fail-open no-op so the scheduler registration
and callers stay stable. It never posts and never raises.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.social_monitor")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


#: Master switch for the social-monitor channel jobs.
SOCIAL_MONITOR_ENABLED: bool = _env_bool("RMI_SOCIAL_MONITOR_ENABLED", True)
#: How often the trends job runs (minutes). Default 60 — 24 calls/day.
SOCIAL_MONITOR_TRENDS_INTERVAL_MIN: int = _env_int("RMI_SOCIAL_MONITOR_TRENDS_INTERVAL_MIN", 60)
#: How often the mentions job runs (minutes). Default 60 — 24 calls/day.
SOCIAL_MONITOR_MENTIONS_INTERVAL_MIN: int = _env_int("RMI_SOCIAL_MONITOR_MENTIONS_INTERVAL_MIN", 60)
#: How often the account-status sweep runs (hours). Default 24 — 15 calls/day.
SOCIAL_MONITOR_STATUS_INTERVAL_HOURS: int = _env_int("RMI_SOCIAL_MONITOR_STATUS_INTERVAL_HOURS", 24)


async def run_trends(ctx: Any = None) -> int:
    """Trends tick — DISABLED: X ingestion owned by rugbrain (RAG).

    The bot no longer fetches X trends (GetXAPI rail retired). X-derived
    intel is consumed through the RAG HTTP API. Fail-open no-op.
    """
    del ctx  # unused — this job never posts
    logger.debug("social_monitor:trends_skipped_x_ingest_owned_by_rugbrain")
    return 0


async def run_mentions(ctx: Any = None) -> int:
    """Mentions tick — DISABLED: X ingestion owned by rugbrain (RAG).

    The bot no longer fetches X mentions (GetXAPI rail retired). X-derived
    intel is consumed through the RAG HTTP API. Fail-open no-op.
    """
    del ctx  # unused — this job never posts
    logger.debug("social_monitor:mentions_skipped_x_ingest_owned_by_rugbrain")
    return 0


async def run_account_status(ctx: Any = None) -> int:
    """Account-status sweep — DISABLED: X ingestion owned by rugbrain (RAG).

    The bot no longer checks X account status (GetXAPI rail retired).
    Fail-open no-op.
    """
    del ctx  # unused — this job never posts
    logger.debug("social_monitor:status_skipped_x_ingest_owned_by_rugbrain")
    return 0


__all__ = [
    "SOCIAL_MONITOR_ENABLED",
    "SOCIAL_MONITOR_MENTIONS_INTERVAL_MIN",
    "SOCIAL_MONITOR_STATUS_INTERVAL_HOURS",
    "SOCIAL_MONITOR_TRENDS_INTERVAL_MIN",
    "run_account_status",
    "run_mentions",
    "run_trends",
]
