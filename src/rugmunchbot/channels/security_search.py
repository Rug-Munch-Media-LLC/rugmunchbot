"""Security keyword search channel — scheduled runner.

Owns the **scheduled security-search** job: every
``RMI_SECURITY_SEARCH_INTERVAL_MINUTES`` (default 30) it runs ONE GetXAPI
``tweet/advanced_search`` against the next rotating security query (via
``tools.security_search``), normalizes the hits into ``NewsArticle`` objects,
dedupes through the shared news-ingest sets, and feeds the survivors into the
RAG ``news_v2`` collection — so RAG "benefits from" breaking security news
even when it comes from an account we don't track.

This is a non-posting ingestion job (like ``x_ingest`` / ``scam_watch``):
nothing is sent to a Telegram chat. It only enriches the RAG corpus. ``ctx``
is accepted for JobQueue compatibility but unused.

Budget doctrine: ONE search call per tick at 30-min cadence = 48 calls/day =
$0.048/day. Fail-open at every step: no GetXAPI key, budget exhausted, empty
sweep, or a RAG outage all log and return without raising — a dead GetXAPI
rail never breaks the scheduler.
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
    """Run one security-search tick: search → normalize → dedupe → feed RAG.

    Returns the number of fresh articles fed to RAG (0 on any failure).
    Fails open: no key, budget exhausted, empty sweep, or RAG outage all log
    and return 0 without raising. ``ctx`` is accepted for JobQueue
    compatibility but unused (no posting).
    """
    del ctx  # unused — this job never posts
    if not SECURITY_SEARCH_ENABLED:
        logger.debug("security_search:disabled")
        return 0
    try:
        from rugmunchbot.tools.security_search import run_security_search as _run
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.warning("security_search:import_failed err=%s", str(exc)[:120])
        return 0
    try:
        counts = await _run()
    except Exception as exc:  # fail-open by contract
        logger.warning("security_search:tick_failed err=%s", str(exc)[:200])
        return 0
    fresh = int(counts.get("fresh") or 0)
    logger.info(
        "security_search:tick query=%.80s fetched=%s articles=%s fresh=%s skipped=%s rag_ingested=%s",
        counts.get("query", ""),
        counts.get("fetched", 0),
        counts.get("articles", 0),
        fresh,
        counts.get("skipped", 0),
        (counts.get("rag") or {}).get("ingested", 0),
    )
    return fresh


__all__ = [
    "SECURITY_SEARCH_ENABLED",
    "SECURITY_SEARCH_INTERVAL_MINUTES",
    "run_security_search",
]
