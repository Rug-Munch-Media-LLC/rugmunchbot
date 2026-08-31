"""TG scraper config — env switches for the all-dialogs channel scraper.

Scrapes EVERY Telegram dialog the headless account is a member of (not just
the curated news/scam channel lists) for (1) scam/shill detection and
(2) RAG ingestion into the ``telegram_social`` collection.

All knobs are env-overridable; defaults are conservative to stay well inside
Telegram flood limits (jittered per-channel pacing, bounded per-channel
message reads, bounded media downloads).
"""

from __future__ import annotations

import os

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.tg_scraper")


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


#: Master switch (env ``RMI_TG_SCRAPER_ENABLED``).
TG_SCRAPER_ENABLED: bool = _env_bool("RMI_TG_SCRAPER_ENABLED", True)

#: Minutes between sweeps (env ``RMI_TG_SCRAPER_INTERVAL_MINUTES``, default 30).
TG_SCRAPER_INTERVAL_MINUTES: int = max(5, _env_int("RMI_TG_SCRAPER_INTERVAL_MINUTES", 30))

#: Lookback window in hours (env ``RMI_TG_SCRAPER_LOOKBACK_HOURS``, default 24).
TG_SCRAPER_LOOKBACK_HOURS: int = max(1, _env_int("RMI_TG_SCRAPER_LOOKBACK_HOURS", 24))

#: Max messages read per dialog per sweep (env ``RMI_TG_SCRAPER_PER_CHANNEL_LIMIT``).
TG_SCRAPER_PER_CHANNEL_LIMIT: int = max(1, _env_int("RMI_TG_SCRAPER_PER_CHANNEL_LIMIT", 100))

#: Max dialogs scraped per sweep (env ``RMI_TG_SCRAPER_MAX_CHANNELS``, default 200).
TG_SCRAPER_MAX_CHANNELS: int = max(1, _env_int("RMI_TG_SCRAPER_MAX_CHANNELS", 200))

#: Jitter bounds between dialog fetches, seconds (anti-ban pacing).
TG_SCRAPER_JITTER_MIN: float = max(0.0, _env_float("RMI_TG_SCRAPER_JITTER_MIN", 1.0))
TG_SCRAPER_JITTER_MAX: float = max(0.0, _env_float("RMI_TG_SCRAPER_JITTER_MAX", 4.0))

#: Cap on honoring a Telegram FloodWaitError sleep request (seconds). Longer
#: waits are truncated — the sweep fails open and skips the channel instead
#: of blocking the whole bot loop.
TG_SCRAPER_FLOOD_WAIT_CAP: float = max(1.0, _env_float("RMI_TG_SCRAPER_FLOOD_WAIT_CAP", 60.0))

#: Download photo media for evidence screenshots (env ``RMI_TG_SCRAPER_DOWNLOAD_MEDIA``).
TG_SCRAPER_DOWNLOAD_MEDIA: bool = _env_bool("RMI_TG_SCRAPER_DOWNLOAD_MEDIA", True)

#: Max media downloads per sweep (bounded — evidence only, not archival).
TG_SCRAPER_MEDIA_BUDGET: int = max(0, _env_int("RMI_TG_SCRAPER_MEDIA_BUDGET", 20))

#: Where downloaded media lands (screenshots attached to scam evidence).
TG_SCRAPER_MEDIA_DIR: str = os.getenv("RMI_TG_SCRAPER_MEDIA_DIR", "/tmp/rugmunchbot/tg_media")  # noqa: S108 — env-configurable evidence dir

#: RAG collection for ALL scraped messages (total social awareness).
TG_SCRAPER_RAG_COLLECTION: str = os.getenv("RMI_TG_SCRAPER_RAG_COLLECTION", "telegram_social")

#: Feed RAG at all (env ``RMI_TG_SCRAPER_RAG``).
TG_SCRAPER_RAG: bool = _env_bool("RMI_TG_SCRAPER_RAG", True)

#: Feed scam detections to threat_intel_v2 + shill evidence (env ``RMI_TG_SCRAPER_THREAT``).
TG_SCRAPER_THREAT: bool = _env_bool("RMI_TG_SCRAPER_THREAT", True)
