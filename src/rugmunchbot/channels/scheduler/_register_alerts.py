"""ALERTS-channel detection rails — split out of ``register_channel_jobs``.

@rmialerts carries exploit / scam / rug / shill-campaign detections ONLY, so
these are the high-signal lanes: shill-campaign watch, the weekly Wall of
Shame, the campaign auto-poster and the RugBrain scanner alerts. One private
registrar per lane; the ``alerts_enabled`` flag is owned by ``_register`` so a
test patches exactly one owner.
"""

from __future__ import annotations

import os
from typing import Any

from rugmunchbot.channels.scheduler import _core
from rugmunchbot.channels.scheduler._tick import _tick_wrapper

logger = _core.logger
_TRUE = ("1", "true", "yes")


def _off(name: str) -> bool:
    """True when an opt-out env knob explicitly disables a lane."""
    return os.getenv(name, "true").lower() not in _TRUE


def _register_shill_watch(job_queue: Any) -> None:
    """Coordinated-shill detection (same token pushed by many accounts)."""
    if _off("RMI_SHILL_WATCH_ENABLED"):
        return
    from rugmunchbot.channels.shill_watch import SHILL_INTERVAL_MIN, run_shill_watch

    job_queue.run_repeating(
        _tick_wrapper("channel_shill_watch", run_shill_watch, envelope=True),
        interval=SHILL_INTERVAL_MIN * 60,
        first=300,
        name="channel_shill_watch",
    )


def _register_wall_of_shame(job_queue: Any) -> None:
    """Weekly top-10 worst KOLs, Sundays 18:00 UTC (+ feed mirror)."""
    from datetime import UTC, time as _wos_time

    from rugmunchbot.channels.shill_watch import post_kol_wall_of_shame

    if _off("RMI_KOL_WALL_OF_SHAME_ENABLED") or not hasattr(job_queue, "run_daily"):
        return
    job_queue.run_daily(
        _tick_wrapper("channel_kol_wall_of_shame", post_kol_wall_of_shame, envelope=True),
        time=_wos_time(hour=18, minute=0, tzinfo=UTC),
        days=(6,),  # Sunday (Python weekday: Monday=0 … Sunday=6)
        name="channel_kol_wall_of_shame",
    )


def _register_shill_campaign(job_queue: Any) -> None:
    """RugBrain clustered shill campaigns → main channel with screenshot."""
    if _off("RMI_SHILL_CAMPAIGN_POST_ENABLED") or not _core._chat_id("main"):
        return
    from rugmunchbot.channels.shill_campaign_post import (
        SHILL_CAMPAIGN_INTERVAL_MIN,
        run_shill_campaign_poster,
    )

    job_queue.run_repeating(
        _tick_wrapper("channel_shill_campaign", run_shill_campaign_poster, envelope=True),
        interval=SHILL_CAMPAIGN_INTERVAL_MIN * 60,
        first=480,
        name="channel_shill_campaign",
    )


def _register_scanner_alerts(job_queue: Any) -> None:
    """RugBrain live detections (``/api/v1/alerts/recent``) → @rmialerts."""
    if _off("RMI_SCANNER_ALERTS_ENABLED"):
        return
    from rugmunchbot.channels.scanner_alerts import (
        SCANNER_ALERT_INTERVAL_MIN,
        run_scanner_alert_watch,
    )

    job_queue.run_repeating(
        _tick_wrapper("channel_scanner_alerts", run_scanner_alert_watch, envelope=True),
        interval=SCANNER_ALERT_INTERVAL_MIN * 60,
        first=240,
        name="channel_scanner_alerts",
    )


def register_alerts_jobs(job_queue: Any, *, alerts_enabled: bool) -> None:
    """Register the @rmialerts detection lanes when flag + chat id are set."""
    if not alerts_enabled or not _core._chat_id("alerts"):
        return
    if not hasattr(job_queue, "run_repeating"):
        return
    _register_shill_watch(job_queue)
    _register_wall_of_shame(job_queue)
    _register_shill_campaign(job_queue)
    _register_scanner_alerts(job_queue)
