"""Evening Wrap — config, env switches, schedule.

The Evening Wrap is the THIRD of three daily reports. It is DISTINCT from
both the Morning Brief and the Midday Market Report: it is the day's RECAP
of the crypto underbelly — the scams that ran, the KOLs shilling, what rug
pulled. It is NOT a market brief and NOT a macro read.

Runs once per day at 20:00 local (America/New_York) by default.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.evening_wrap")


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


EVENING_WRAP_ENABLED: bool = _env_bool("RMI_EVENING_WRAP_ENABLED", True)

EVENING_WRAP_FEED_ENABLED: bool = _env_bool("RMI_EVENING_WRAP_FEED_ENABLED", True)

#: Telegram char budget per part (4096 limit minus formatting headroom).
EVENING_WRAP_MAX_CHARS: int = 3900

EVENING_WRAP_TZ: str = _env_str("RMI_EVENING_WRAP_TZ", "America/New_York")

#: Evening slot — configurable, defaults 20:00 local (America/New_York).
EVENING_WRAP_TIME: str = _env_str("RMI_EVENING_WRAP_TIME", "20:00")

#: Branded graphic for the wrap (operator original, wired via
#: ``support.post_graphics``). The operator is producing
#: ``rmi-evening-wrap``; until it lands the brand default applies.
EVENING_WRAP_GRAPHIC: str = _env_str("RMI_EVENING_WRAP_GRAPHIC", "rmi-evening-wrap.jpeg")


def evening_wrap_run_time() -> Any:
    """``datetime.time`` (tz-aware) for the scheduler's daily trigger.

    Parses ``RMI_EVENING_WRAP_TIME`` in ``RMI_EVENING_WRAP_TZ``; any parse
    failure falls back to 20:00 in the configured (default ET) zone — the
    job must always schedule.
    """
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    try:
        hour, minute = (int(p) for p in EVENING_WRAP_TIME.split(":", 1))
        hour, minute = max(0, min(23, hour)), max(0, min(59, minute))
    except (ValueError, TypeError):
        logger.warning("evening_wrap:bad_time value=%s — using 20:00", EVENING_WRAP_TIME)
        hour, minute = 20, 0
    try:
        tz = ZoneInfo(EVENING_WRAP_TZ)
    except Exception as exc:
        logger.warning("evening_wrap:bad_tz value=%s err=%s", EVENING_WRAP_TZ, exc)
        tz = ZoneInfo("America/New_York")
    return dtime(hour=hour, minute=minute, tzinfo=tz)
