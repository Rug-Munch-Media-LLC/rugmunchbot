"""Daily rug update — config, env switches, schedule."""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.daily_rug_update")


def _env_bool(name: str, default: bool) -> bool:
    raw = os_getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_str(name: str, default: str) -> str:
    raw = os_getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip()


# stdlib import kept below the helpers so the module reads top-down; alias
# avoids shadowing in tests that monkeypatch os.environ via patch.dict.
import os as _os  # noqa: E402

os_getenv = _os.getenv

DAILY_RUG_UPDATE_ENABLED: bool = _env_bool("RMI_DAILY_RUG_UPDATE_ENABLED", True)

DAILY_RUG_UPDATE_FEED_ENABLED: bool = _env_bool("RMI_DAILY_RUG_UPDATE_FEED_ENABLED", True)

#: Telegram char budget per part (4096 limit minus formatting headroom).
DAILY_RUG_UPDATE_MAX_CHARS: int = 3900

DAILY_RUG_UPDATE_TZ: str = _env_str("RMI_DAILY_RUG_UPDATE_TZ", "America/New_York")

#: Midday slot — configurable, defaults 12:30 local (America/New_York).
DAILY_RUG_UPDATE_TIME: str = _env_str("RMI_DAILY_RUG_UPDATE_TIME", "12:30")


def rug_update_run_time() -> Any:
    """``datetime.time`` (tz-aware) for the scheduler's daily trigger.

    Parses ``RMI_DAILY_RUG_UPDATE_TIME`` in ``RMI_DAILY_RUG_UPDATE_TZ``;
    any parse failure falls back to 12:30 in the configured (default ET)
    zone — the job must always schedule.
    """
    from datetime import time as dtime
    from zoneinfo import ZoneInfo

    try:
        hour, minute = (int(p) for p in DAILY_RUG_UPDATE_TIME.split(":", 1))
        hour, minute = max(0, min(23, hour)), max(0, min(59, minute))
    except (ValueError, TypeError):
        logger.warning("daily_rug_update:bad_time value=%s — using 12:30", DAILY_RUG_UPDATE_TIME)
        hour, minute = 12, 30
    try:
        tz = ZoneInfo(DAILY_RUG_UPDATE_TZ)
    except Exception as exc:
        logger.warning("daily_rug_update:bad_tz value=%s err=%s", DAILY_RUG_UPDATE_TZ, exc)
        tz = ZoneInfo("America/New_York")
    return dtime(hour=hour, minute=minute, tzinfo=tz)
