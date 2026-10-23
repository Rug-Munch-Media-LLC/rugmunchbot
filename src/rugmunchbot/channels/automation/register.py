"""Register the four content-automation lanes on the bot JobQueue."""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.scheduler._tick import _tick_wrapper
from rugmunchbot.support.logging import get_logger

from .runner import run_lane

logger = get_logger("rmi_bot.channels.automation")

_OPT_IN = ("1", "true", "yes", "on")

#: lane → (interval env, default minutes, first-run offset in seconds)
_LANES: dict[str, tuple[str, int, int]] = {
    "move": ("RMI_CMOVE_INTERVAL_MIN", 30, 265),
    "liquidation": ("RMI_CLIQ_INTERVAL_MIN", 20, 275),
    "odds": ("RMI_CODDS_INTERVAL_MIN", 60, 285),
    "call": ("RMI_CCALL_INTERVAL_MIN", 45, 295),
}


def _minutes(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def register_content_jobs(app: Any) -> None:
    """Add one repeating job per lane. ``0`` minutes disables that lane.

    Registration itself is on by default (``RMI_CONTENT_AUTOMATION``); every
    tick still resolves to a dry run unless ``RMI_SMARTMONEY_POST`` opts in.
    """
    if os.getenv("RMI_CONTENT_AUTOMATION", "1").strip().lower() not in _OPT_IN:
        logger.info("channels:automation_disabled reason=flag_off")
        return
    job_queue = getattr(app, "job_queue", None)
    if job_queue is None:
        logger.warning("channels:automation_no_jobqueue")
        return
    for lane, (env_name, default, first) in _LANES.items():
        minutes = _minutes(env_name, default)
        if minutes <= 0:
            logger.info("channels:automation_lane_disabled lane=%s", lane)
            continue

        async def tick(ctx: Any, lane: str = lane) -> dict[str, Any]:
            return await run_lane(lane, bot=ctx.bot)

        job_queue.run_repeating(
            _tick_wrapper(f"content_{lane}", tick, envelope=True),
            interval=minutes * 60,
            first=first,
            name=f"content_{lane}",
        )
    logger.info("channels:automation_registered lanes=%s", ",".join(_LANES))
