"""The Daily Rug — config, env switches, once-daily schedule.

"The Daily Rug — Morning Market Brief" is the flagship longform morning
edition: the best stories from the previous day plus a forward-looking
"Things to look out for" section. It runs EXACTLY ONCE PER DAY (a date
guard in ``_publish`` makes the job idempotent — a restart, a manual
re-run, or a duplicate scheduler tick can never double-post).
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.daily_rug")


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


DAILY_RUG_ENABLED: bool = _env_bool("RMI_DAILY_RUG_ENABLED", True)

DAILY_RUG_FEED_ENABLED: bool = _env_bool("RMI_DAILY_RUG_FEED_ENABLED", True)

#: Telegram char budget per part (4096 limit minus formatting headroom).
DAILY_RUG_MAX_CHARS: int = 3900

DAILY_RUG_TZ: str = _env_str("RMI_DAILY_RUG_TZ", "America/New_York")

#: Morning slot — configurable, defaults 07:30 local (America/New_York).
#: Deliberately offset from The Morning Rug (07:00) so the two flagship
#: posts never collide on the channel.
DAILY_RUG_TIME: str = _env_str("RMI_DAILY_RUG_TIME", "07:30")

#: Branded logo for the brief (served from the feed site's brand path).
DAILY_RUG_LOGO_URL: str = _env_str(
    "RMI_DAILY_RUG_LOGO_URL", "https://rugmunch.io/brand/daily-rug-logo.jpeg"
)


def daily_rug_run_time() -> Any:
    """``datetime.time`` (tz-aware) for the scheduler's once-daily trigger.

    Parses ``RMI_DAILY_RUG_TIME`` in ``RMI_DAILY_RUG_TZ``; any parse failure
    falls back to 07:30 in the configured (default ET) zone — the job must
    always schedule.
    """
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    try:
        hour, minute = (int(p) for p in DAILY_RUG_TIME.split(":", 1))
        hour, minute = max(0, min(23, hour)), max(0, min(59, minute))
    except (ValueError, TypeError):
        logger.warning("daily_rug:bad_time value=%s — using 07:30", DAILY_RUG_TIME)
        hour, minute = 7, 30
    try:
        tz = ZoneInfo(DAILY_RUG_TZ)
    except Exception as exc:
        logger.warning("daily_rug:bad_tz value=%s err=%s", DAILY_RUG_TZ, exc)
        tz = ZoneInfo("America/New_York")
    return dtime(hour=hour, minute=minute, tzinfo=tz)
