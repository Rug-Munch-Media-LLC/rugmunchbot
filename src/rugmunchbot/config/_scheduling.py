#!/usr/bin/env python3
"""Channel content scheduling — env-configurable switches and cadences.

Master + per-channel switches and posting intervals for the scheduled
channel content jobs (channels/scheduler.py). All values are env-driven so
operators can tune cadence without a code deploy.

All ``_read_*`` functions here are UNCACHED primitives: the ``@lru_cache``
``_resolve_*`` wrappers live in the package ``__init__`` so a package reload
resets caches exactly like the old single-module config did.
"""

from __future__ import annotations

import os


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Parse a positive float env var (e.g. 0.5 = 30 minutes)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.1, float(raw.strip()))
    except (TypeError, ValueError):
        return default


def _read_channel_jobs_enabled() -> bool:
    return _env_bool("RMI_CHANNELS_ENABLED", True)


def _read_news_channel_enabled() -> bool:
    return _env_bool("RMI_NEWS_CHANNEL_ENABLED", True)


def _read_alerts_channel_enabled() -> bool:
    return _env_bool("RMI_ALERTS_CHANNEL_ENABLED", True)


def _read_alpha_channel_enabled() -> bool:
    return _env_bool("RMI_ALPHA_CHANNEL_ENABLED", True)


def _read_news_channel_interval_hours() -> float:
    return _env_float("RMI_CHANNEL_NEWS_INTERVAL_HOURS", 4)


def _read_alpha_channel_interval_hours() -> int:
    return _env_int("RMI_CHANNEL_ALPHA_INTERVAL_HOURS", 1)


def _read_alerts_channel_interval_minutes() -> int:
    return _env_int("RMI_CHANNEL_ALERTS_INTERVAL_MINUTES", 30)


def _read_news_channel_limit() -> int:
    return _env_int("RMI_CHANNEL_NEWS_LIMIT", 5)


def _read_alpha_channel_limit() -> int:
    return _env_int("RMI_CHANNEL_ALPHA_LIMIT", 5)


def _read_alerts_channel_limit() -> int:
    return _env_int("RMI_CHANNEL_ALERTS_LIMIT", 5)


def _read_scan_channel_enabled() -> bool:
    return _env_bool("RMI_SCANS_CHANNEL_ENABLED", True)


def _read_scan_channel_interval_minutes() -> int:
    return _env_int("RMI_CHANNEL_SCANS_INTERVAL_MINUTES", 60)


def _read_scan_channel_limit() -> int:
    return _env_int("RMI_CHANNEL_SCANS_LIMIT", 10)


def _read_scan_channel_tick_limit() -> int:
    return _env_int("RMI_CHANNEL_SCANS_TICK_LIMIT", 3)


def _read_updates_channel_enabled() -> bool:
    """@rmiupdates changelog digest (daily system progress). Default ON —
    the job no-ops when the channel id cannot be resolved."""
    return _env_bool("RMI_UPDATES_CHANNEL_ENABLED", True)


def _read_scam_alerts_publisher_enabled() -> bool:
    return _env_bool("RMI_SCAM_ALERTS_PUBLISHER_ENABLED", True)

def _read_scam_alerts_publisher_interval_minutes() -> int:
    return _env_int("RMI_SCAM_ALERTS_PUBLISHER_INTERVAL_MINUTES", 30)

def _read_updates_post_hour_utc() -> int:
    """Hour (0-23 UTC) the daily @rmiupdates changelog fires."""
    raw = os.getenv("RMI_UPDATES_HOUR_UTC")
    if raw is None:
        return 9
    try:
        return min(23, max(0, int(raw.strip())))
    except (TypeError, ValueError):
        return 9
