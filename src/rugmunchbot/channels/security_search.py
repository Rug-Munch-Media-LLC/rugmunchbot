"""Security keyword search channel — RETIRED (X ingestion owned by rugbrain).

X ingestion (fetch, normalize, embed, ingest) is owned by **rugbrain** (the
RAG service). The bot no longer runs GetXAPI keyword searches; X-derived
intel is consumed through the RAG HTTP API (``tools/rag_lookup.py``).

This module is retained as a fail-open no-op so the scheduler registration
and callers stay stable. It never posts and never raises.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.security_search")


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


#: Master switch for the security-search channel job.
SECURITY_SEARCH_ENABLED: bool = _env_bool("RMI_SECURITY_SEARCH_ENABLED", True)
#: How often the security-search job runs (minutes). Default 30 — the task's
#: cadence (48 calls/day = $0.048/day).
SECURITY_SEARCH_INTERVAL_MINUTES: int = _env_int("RMI_SECURITY_SEARCH_INTERVAL_MINUTES", 30)


async def run_security_search(ctx: Any = None) -> int:
    """Security keyword search — DISABLED: X ingestion owned by rugbrain (RAG).

    The bot no longer runs GetXAPI keyword searches (X-fetch rail retired).
    X-derived intel is consumed through the RAG HTTP API. Fail-open no-op so
    the scheduler registration and callers stay stable.
    """
    del ctx  # unused — this job never posts
    logger.debug("security_search:skipped_x_ingest_owned_by_rugbrain")
    return 0


__all__ = [
    "SECURITY_SEARCH_ENABLED",
    "SECURITY_SEARCH_INTERVAL_MINUTES",
    "run_security_search",
]
