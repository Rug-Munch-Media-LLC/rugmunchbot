"""TG all-dialogs scraper (package).

Scrapes EVERY Telegram dialog the headless account is a member of for
(1) scam/shill detection and (2) RAG ingestion into ``telegram_social``.

Layout:
  * ``_config.py``  — env switches + rate-limit/jitter knobs
  * ``_scrape.py``  — dialog listing, message scraping, FloodWait fail-open
  * ``_detect.py``  — token mentions, scam patterns, known-scammer probe
  * ``_ingest.py``  — RAG ingestion (telegram_social) + threat persistence
  * ``_runner.py``  — periodic run_tg_scraper entry point
"""

from __future__ import annotations

from rugmunchbot.channels.tg_scraper._config import (
    TG_SCRAPER_DOWNLOAD_MEDIA as TG_SCRAPER_DOWNLOAD_MEDIA,
    TG_SCRAPER_ENABLED as TG_SCRAPER_ENABLED,
    TG_SCRAPER_INTERVAL_MINUTES as TG_SCRAPER_INTERVAL_MINUTES,
    TG_SCRAPER_JITTER_MAX as TG_SCRAPER_JITTER_MAX,
    TG_SCRAPER_JITTER_MIN as TG_SCRAPER_JITTER_MIN,
    TG_SCRAPER_LOOKBACK_HOURS as TG_SCRAPER_LOOKBACK_HOURS,
    TG_SCRAPER_MAX_CHANNELS as TG_SCRAPER_MAX_CHANNELS,
    TG_SCRAPER_MEDIA_BUDGET as TG_SCRAPER_MEDIA_BUDGET,
    TG_SCRAPER_PER_CHANNEL_LIMIT as TG_SCRAPER_PER_CHANNEL_LIMIT,
    TG_SCRAPER_RAG as TG_SCRAPER_RAG,
    TG_SCRAPER_RAG_COLLECTION as TG_SCRAPER_RAG_COLLECTION,
    TG_SCRAPER_THREAT as TG_SCRAPER_THREAT,
)
from rugmunchbot.channels.tg_scraper._detect import (
    SCAM_PATTERNS as SCAM_PATTERNS,
    Detection as Detection,
    ScamHit as ScamHit,
    detect_message as detect_message,
    detect_scam_patterns as detect_scam_patterns,
    detection_summary as detection_summary,
    extract_mentions as extract_mentions,
    is_known_scammer as is_known_scammer,
)
from rugmunchbot.channels.tg_scraper._ingest import (
    ingest_to_rag as ingest_to_rag,
    message_key as message_key,
    persist_scam_detections as persist_scam_detections,
)
from rugmunchbot.channels.tg_scraper._runner import (
    run_tg_scraper as run_tg_scraper,
)
from rugmunchbot.channels.tg_scraper._scrape import (
    TgMessage as TgMessage,
    list_dialogs as list_dialogs,
    scrape_all_dialogs as scrape_all_dialogs,
    scrape_dialog as scrape_dialog,
)
