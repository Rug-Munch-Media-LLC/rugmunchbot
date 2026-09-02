"""X/Twitter content ingestion channel — scheduled runner.

Owns the **scheduled X-ingest** job: every ``X_INGEST_INTERVAL_MINUTES`` it
pulls tweets from the monitored crypto-security accounts (via
``tools.x_ingest``), normalizes them into ``NewsArticle`` objects, dedupes
through the shared news-ingest sets, and feeds the survivors into the RAG
``news_v2`` collection — so RAG "benefits from" the X lane exactly like it
does the TG news lane.

This is a non-posting ingestion job (like ``scam_watch``): nothing is sent
to a Telegram chat. It only enriches the RAG corpus. ``ctx`` is accepted for
JobQueue compatibility but unused.

Fail-open at every step: no ApiPass key, budget exhausted, empty sweep, or a
RAG outage all log and return without raising — a dead X rail never breaks
the scheduler.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.x_ingest")


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


#: Master switch for the X-ingest channel job.
X_INGEST_ENABLED: bool = _env_bool("RMI_X_INGEST_ENABLED", True)
#: How often the X-ingest job runs (minutes). Default 5 — the task's cadence.
X_INGEST_INTERVAL_MINUTES: int = _env_int("RMI_X_INGEST_INTERVAL_MINUTES", 5)


async def run_x_ingest(ctx: Any = None) -> int:
    """Run one X-ingest tick: fetch → normalize → dedupe → feed RAG.

    Returns the number of fresh articles fed to RAG (0 on any failure).
    Fails open: no key, budget exhausted, empty sweep, or RAG outage all log
    and return 0 without raising. ``ctx`` is accepted for JobQueue
    compatibility but unused (no posting).
    """
    del ctx  # unused — this job never posts
    if not X_INGEST_ENABLED:
        logger.debug("x_ingest:disabled")
        return 0
    try:
        from rugmunchbot.tools.x_ingest import ingest_x_news
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.warning("x_ingest:import_failed err=%s", str(exc)[:120])
        return 0
    try:
        counts = await ingest_x_news()
    except Exception as exc:  # fail-open by contract
        logger.warning("x_ingest:tick_failed err=%s", str(exc)[:200])
        return 0
    fresh = int(counts.get("fresh") or 0)
    logger.info(
        "x_ingest:tick fetched=%s articles=%s fresh=%s skipped=%s rag_ingested=%s",
        counts.get("fetched", 0),
        counts.get("articles", 0),
        fresh,
        counts.get("skipped", 0),
        (counts.get("rag") or {}).get("ingested", 0),
    )
    return fresh


__all__ = [
    "X_INGEST_ENABLED",
    "X_INGEST_INTERVAL_MINUTES",
    "run_x_ingest",
]
