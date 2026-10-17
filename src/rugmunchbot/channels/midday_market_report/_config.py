"""Midday Market Report — config, env switches, schedule.

The Midday Market Report is the SECOND of three daily reports. It is
DISTINCT from the Morning Brief: it covers BROADER markets (equities,
DXY, gold, rates) and their effect on crypto, plus macro (Fed, jobless
claims, CPI). It is NOT a second crypto-story list.

Runs once per day at 12:30 local (America/New_York) by default.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.midday_market_report")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


MIDDAY_REPORT_ENABLED: bool = _env_bool("RMI_MIDDAY_REPORT_ENABLED", True)

MIDDAY_REPORT_FEED_ENABLED: bool = _env_bool("RMI_MIDDAY_REPORT_FEED_ENABLED", True)

#: Telegram char budget per part (4096 limit minus formatting headroom).
MIDDAY_REPORT_MAX_CHARS: int = 3900

MIDDAY_REPORT_TZ: str = _env_str("RMI_MIDDAY_REPORT_TZ", "America/New_York")

#: Midday slot — configurable, defaults 12:30 local (America/New_York).
MIDDAY_REPORT_TIME: str = _env_str("RMI_MIDDAY_REPORT_TIME", "12:30")

#: Branded graphic for the report (operator original, wired via
#: ``support.post_graphics``). The operator is producing
#: ``rmi-midday-market-report``; until it lands the brand default applies.
MIDDAY_REPORT_GRAPHIC: str = _env_str("RMI_MIDDAY_REPORT_GRAPHIC", "rmi-midday-market-report.jpeg")


def midday_report_run_time() -> Any:
    """``datetime.time`` (tz-aware) for the scheduler's daily trigger.

    Parses ``RMI_MIDDAY_REPORT_TIME`` in ``RMI_MIDDAY_REPORT_TZ``; any parse
    failure falls back to 12:30 in the configured (default ET) zone — the
    job must always schedule.
    """
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    try:
        hour, minute = (int(p) for p in MIDDAY_REPORT_TIME.split(":", 1))
        hour, minute = max(0, min(23, hour)), max(0, min(59, minute))
    except (ValueError, TypeError):
        logger.warning("midday_report:bad_time value=%s — using 12:30", MIDDAY_REPORT_TIME)
        hour, minute = 12, 30
    try:
        tz = ZoneInfo(MIDDAY_REPORT_TZ)
    except Exception as exc:
        logger.warning("midday_report:bad_tz value=%s err=%s", MIDDAY_REPORT_TZ, exc)
        tz = ZoneInfo("America/New_York")
    return dtime(hour=hour, minute=minute, tzinfo=tz)
