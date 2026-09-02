"""Trending topics + mention monitoring channel — scheduled runner.

Owns the **scheduled social-monitor** jobs:

* **Trends** — every ``RMI_SOCIAL_MONITOR_TRENDS_INTERVAL_MIN`` (default 60)
  it runs ONE GetXAPI ``/trends`` call (via ``tools.social_monitor``), filters
  for crypto-related trends, and caches the result in Redis 30 min. 24
  calls/day = $0.024/day.
* **Mentions** — every ``RMI_SOCIAL_MONITOR_MENTIONS_INTERVAL_MIN`` (default
  60) it runs ONE GetXAPI ``/user/mentions`` call for ``@cryptorugmuncher``,
  normalizes the hits into ``NewsArticle`` objects, dedupes through the shared
  news-ingest sets, and feeds the survivors into the RAG ``news_v2``
  collection. 24 calls/day = $0.024/day.
* **Account status** — every ``RMI_SOCIAL_MONITOR_STATUS_INTERVAL_HOURS``
  (default 24) it checks the status of every monitored security account (ONE
  GetXAPI ``/user/status`` call each, 15 accounts) and logs a WARNING when an
  account is suspended. 15 calls/day = $0.015/day.

Total: 63 calls/day = $0.063/day.

This is a non-posting ingestion job (like ``x_ingest`` / ``scam_watch`` /
``security_search``): nothing is sent to a Telegram chat. It only enriches the
RAG corpus and logs account health. ``ctx`` is accepted for JobQueue
compatibility but unused.

Budget doctrine: 63 calls/day at $0.001/call = $0.063/day. Fail-open at every
step: no GetXAPI key, budget exhausted, empty sweep, or a RAG outage all log
and return without raising — a dead GetXAPI rail never breaks the scheduler.
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
    """Run one trends tick: fetch + filter crypto trends (no posting).

    Returns the number of crypto trends found (0 on any failure). Fails open:
    no key, budget exhausted, or API error all log and return 0 without
    raising. ``ctx`` is accepted for JobQueue compatibility but unused.
    """
    del ctx  # unused — this job never posts
    if not SOCIAL_MONITOR_ENABLED:
        logger.debug("social_monitor:trends_disabled")
        return 0
    try:
        from rugmunchbot.tools.social_monitor import fetch_crypto_trends
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.warning("social_monitor:trends_import_failed err=%s", str(exc)[:120])
        return 0
    try:
        trends = await fetch_crypto_trends()
    except Exception as exc:  # fail-open by contract
        logger.warning("social_monitor:trends_tick_failed err=%s", str(exc)[:200])
        return 0
    logger.info("social_monitor:trends_tick crypto_trends=%s", len(trends))
    return len(trends)


async def run_mentions(ctx: Any = None) -> int:
    """Run one mentions tick: mentions → normalize → dedupe → feed RAG.

    Returns the number of fresh mention articles fed to RAG (0 on any
    failure). Fails open: no key, budget exhausted, empty sweep, or RAG outage
    all log and return 0 without raising. ``ctx`` is accepted for JobQueue
    compatibility but unused (no posting).
    """
    del ctx  # unused — this job never posts
    if not SOCIAL_MONITOR_ENABLED:
        logger.debug("social_monitor:mentions_disabled")
        return 0
    try:
        from rugmunchbot.tools.social_monitor import run_mentions as _run
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.warning("social_monitor:mentions_import_failed err=%s", str(exc)[:120])
        return 0
    try:
        counts = await _run()
    except Exception as exc:  # fail-open by contract
        logger.warning("social_monitor:mentions_tick_failed err=%s", str(exc)[:200])
        return 0
    fresh = int(counts.get("fresh") or 0)
    logger.info(
        "social_monitor:mentions_tick username=%s fetched=%s articles=%s fresh=%s skipped=%s rag_ingested=%s",
        counts.get("username", ""),
        counts.get("fetched", 0),
        counts.get("articles", 0),
        fresh,
        counts.get("skipped", 0),
        (counts.get("rag") or {}).get("ingested", 0),
    )
    return fresh


async def run_account_status(ctx: Any = None) -> int:
    """Run one account-status sweep: check every monitored account.

    Returns the number of accounts checked (0 on any failure). Fails open: no
    key, budget exhausted, or API error on any account all log and are skipped
    without raising. ``ctx`` is accepted for JobQueue compatibility but unused
    (no posting).
    """
    del ctx  # unused — this job never posts
    if not SOCIAL_MONITOR_ENABLED:
        logger.debug("social_monitor:status_disabled")
        return 0
    try:
        from rugmunchbot.tools.social_monitor import run_account_status_sweep
    except Exception as exc:  # pragma: no cover — import failure fails open
        logger.warning("social_monitor:status_import_failed err=%s", str(exc)[:120])
        return 0
    try:
        counts = await run_account_status_sweep()
    except Exception as exc:  # fail-open by contract
        logger.warning("social_monitor:status_tick_failed err=%s", str(exc)[:200])
        return 0
    checked = int(counts.get("checked") or 0)
    logger.info(
        "social_monitor:status_tick checked=%s suspended=%s",
        checked,
        counts.get("suspended", 0),
    )
    return checked


__all__ = [
    "SOCIAL_MONITOR_ENABLED",
    "SOCIAL_MONITOR_MENTIONS_INTERVAL_MIN",
    "SOCIAL_MONITOR_STATUS_INTERVAL_HOURS",
    "SOCIAL_MONITOR_TRENDS_INTERVAL_MIN",
    "run_account_status",
    "run_mentions",
    "run_trends",
]
