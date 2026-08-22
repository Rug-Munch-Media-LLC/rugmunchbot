"""Daily-summary config — env switches, schedule, char budget."""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


logger = get_logger("rmi_bot.channels.daily_market_summary")


def _env_bool_alias(new: str, old: str, default: bool) -> bool:
    """Read the canonical (renamed) env var, falling back to the legacy name.

    ``RMI_DAILY_MARKET_BRIEF_*`` is canonical; ``RMI_DAILY_MARKET_SUMMARY_*``
    keeps working as an alias so existing deployments don't need edits. The
    new name wins when both are set.
    """
    raw = os.getenv(new)
    if raw is None:
        raw = os.getenv(old)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int_alias(new: str, old: str, default: int) -> int:
    raw = os.getenv(new)
    if raw is None:
        raw = os.getenv(old)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_str_alias(new: str, old: str, default: str) -> str:
    raw = os.getenv(new)
    if raw is None:
        raw = os.getenv(old)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


DAILY_MARKET_SUMMARY_ENABLED: bool = _env_bool_alias(
    "RMI_DAILY_MARKET_BRIEF_ENABLED", "RMI_DAILY_MARKET_SUMMARY_ENABLED", True
)


DAILY_MARKET_SUMMARY_MAX_CHARS: int = _env_int_alias(
    "RMI_DAILY_MARKET_BRIEF_MAX_CHARS", "RMI_DAILY_MARKET_SUMMARY_MAX_CHARS", 3900
)


DAILY_MARKET_SUMMARY_FEED_ENABLED: bool = _env_bool_alias(
    "RMI_DAILY_MARKET_BRIEF_FEED_ENABLED", "RMI_DAILY_MARKET_SUMMARY_FEED_ENABLED", True
)


DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED: bool = _env_bool_alias(
    "RMI_DAILY_MARKET_BRIEF_WEB_SIGNALS_ENABLED",
    "RMI_DAILY_MARKET_SUMMARY_WEB_SIGNALS_ENABLED",
    True,
)


DAILY_MARKET_SUMMARY_TZ: str = _env_str_alias(
    "RMI_DAILY_MARKET_BRIEF_TZ", "RMI_DAILY_MARKET_SUMMARY_TZ", "America/New_York"
)


DAILY_MARKET_SUMMARY_TIME: str = _env_str_alias(
    "RMI_DAILY_MARKET_BRIEF_TIME", "RMI_DAILY_MARKET_SUMMARY_TIME", "07:00"
)


def summary_run_time() -> Any:
    """``datetime.time`` (tz-aware) for the APScheduler daily trigger.

    Parses ``RMI_DAILY_MARKET_SUMMARY_TIME`` in
    ``RMI_DAILY_MARKET_SUMMARY_TZ``; any parse failure falls back to 07:00 in
    the configured (default ET) zone — the job must always schedule.
    """
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    try:
        hour, minute = (int(p) for p in DAILY_MARKET_SUMMARY_TIME.split(":", 1))
        hour, minute = max(0, min(23, hour)), max(0, min(59, minute))
    except (ValueError, TypeError):
        logger.warning(
            "daily_market_summary:bad_time value=%s — using 07:00",
            DAILY_MARKET_SUMMARY_TIME,
        )
        hour, minute = 7, 0
    try:
        tz = ZoneInfo(DAILY_MARKET_SUMMARY_TZ)
    except Exception as exc:  # zoneinfo raises ZoneInfoNotFoundError for bad tz
        logger.warning("daily_market_summary:bad_tz value=%s err=%s", DAILY_MARKET_SUMMARY_TZ, exc)
        tz = ZoneInfo("America/New_York")
    return dtime(hour=hour, minute=minute, tzinfo=tz)
