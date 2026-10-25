"""KOL + scammer-intel rails — split out of ``register_channel_jobs``.

The scoreboard, the CT-timeline shill sweep, the daily KOL refresh and the
scammer-group warning all read governed ApiPass data and post to @rmialerts.
Each is budget-bounded and fail-open, so an unconfigured key skips the lane
instead of blocking startup.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._tick import _tick_wrapper

_TRUE = ("1", "true", "yes")


def _on(name: str, *, fallback: str = "true") -> bool:
    """Opt-out env knob, with an optional legacy alias for the same lane."""
    return os.getenv(name, os.getenv(fallback, "true")).lower() in _TRUE


def _register_kol_scoreboard(job_queue: Any) -> None:
    """Per-KOL report cards: calls, deleted tweets, name changes, wallets."""
    if not _on("RMI_KOL_SCOREBOARD_ENABLED", fallback="RMI_KOL_SHAME_ENABLED"):
        return
    from rugmunchbot.channels.kol_scoreboard import (
        KOL_SCOREBOARD_INTERVAL_MIN,
        run_kol_scoreboard_watch,
    )

    job_queue.run_repeating(
        _tick_wrapper("channel_kol_scoreboard", run_kol_scoreboard_watch, envelope=True),
        interval=KOL_SCOREBOARD_INTERVAL_MIN * 60,
        first=360,
        name="channel_kol_scoreboard",
    )


def _register_ct_shill(job_queue: Any) -> None:
    """CT-timeline sweep: rotates influential accounts, embeds shills."""
    if not _on("RMI_CT_SHILL_ENABLED"):
        return
    from rugmunchbot.channels.ct_shill_watch import (
        CT_SHILL_INTERVAL_MIN,
        run_ct_shill_watch,
    )

    job_queue.run_repeating(
        _tick_wrapper("channel_ct_shill_watch", run_ct_shill_watch, envelope=True),
        interval=CT_SHILL_INTERVAL_MIN * 60,
        first=420,
        name="channel_ct_shill_watch",
    )


def _register_kol_refresh(job_queue: Any) -> None:
    """Daily rescore of every Wall of Shame entry + new-candidate discovery."""
    if not _on("RMI_KOL_DAILY_REFRESH_ENABLED"):
        return
    from rugmunchbot.channels.ct_shill_watch import (
        KOL_DAILY_INTERVAL_MIN,
        run_kol_daily_refresh,
    )

    job_queue.run_repeating(
        _tick_wrapper("channel_kol_daily_refresh", run_kol_daily_refresh, envelope=True),
        interval=KOL_DAILY_INTERVAL_MIN * 60,
        first=600,
        name="channel_kol_daily_refresh",
    )


def _register_scammer_warning(job_queue: Any) -> None:
    """A seeded scammer-group member shilled a token → warning to @rmialerts."""
    if not _on("RMI_SCAMMER_WARNING_ENABLED"):
        return
    from rugmunchbot.channels.scammer_warning import (
        SCAMMER_WARNING_INTERVAL_MIN,
        run_scammer_warning_watch,
    )

    job_queue.run_repeating(
        _tick_wrapper("channel_scammer_warning", run_scammer_warning_watch, envelope=True),
        interval=SCAMMER_WARNING_INTERVAL_MIN * 60,
        first=540,
        name="channel_scammer_warning",
    )


def register_kol_jobs(job_queue: Any, *, alerts_enabled: bool) -> None:
    """Register the KOL/scammer rails when flag + alerts chat id are set."""
    if not alerts_enabled or not _core._chat_id("alerts"):
        return
    if not hasattr(job_queue, "run_repeating"):
        return
    _register_kol_scoreboard(job_queue)
    _register_ct_shill(job_queue)
    _register_kol_refresh(job_queue)
    _register_scammer_warning(job_queue)
