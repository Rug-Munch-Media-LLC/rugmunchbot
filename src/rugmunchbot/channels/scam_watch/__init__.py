"""Scam-watch channel pipeline (package).

Modularized from the former single-module ``scam_watch.py`` (2026-08-21).
This ``__init__`` is a pure re-export shim: every public name and signature
is unchanged, and callers keep importing ``channels.scam_watch`` exactly as
before.

Layout:
  * ``_config.py``   — env switches, channel registry, dialog heuristics
  * ``_discover.py`` — headless client, discovery, categorization, sweep
  * ``_classify.py`` — entity classification + token-address extraction
  * ``_pipeline.py`` — seen-cache, scan queue, scanner/RAG feeds
  * ``_runner.py``   — periodic run_scam_watch entry point
"""

from __future__ import annotations

from rugmunchbot.channels.scam_watch._classify import (
    classify_entity as classify_entity,
    extract_token_addresses as extract_token_addresses,
)
from rugmunchbot.channels.scam_watch._config import (
    _DEFAULT_SCAM_CHANNELS as _DEFAULT_SCAM_CHANNELS,
    _DIALOG_HEURISTICS as _DIALOG_HEURISTICS,
    _OWN_EXCLUDE_KEYWORDS as _OWN_EXCLUDE_KEYWORDS,
    SCAM_CHANNELS as SCAM_CHANNELS,
    SCAM_WATCH_ENABLED as SCAM_WATCH_ENABLED,
    SCAM_WATCH_ENQUEUE as SCAM_WATCH_ENQUEUE,
    SCAM_WATCH_EXCLUDE as SCAM_WATCH_EXCLUDE,
    SCAM_WATCH_INCLUDE_WATCHERS as SCAM_WATCH_INCLUDE_WATCHERS,
    SCAM_WATCH_INTERVAL_MINUTES as SCAM_WATCH_INTERVAL_MINUTES,
    SCAM_WATCH_MAX_DISCOVERED as SCAM_WATCH_MAX_DISCOVERED,
    SCAM_WATCH_PER_CHANNEL_LIMIT as SCAM_WATCH_PER_CHANNEL_LIMIT,
    SCAM_WATCH_QUEUE_LIMIT as SCAM_WATCH_QUEUE_LIMIT,
    SCAM_WATCH_SCAN_LIMIT as SCAM_WATCH_SCAN_LIMIT,
    SCAM_WATCH_SWEEP_HOURS as SCAM_WATCH_SWEEP_HOURS,
    ScamSourceType as ScamSourceType,
    _env_bool as _env_bool,
    _env_int as _env_int,
    _env_list as _env_list,
    logger as logger,
    re_split as re_split,
)
from rugmunchbot.channels.scam_watch._discover import (
    _as_utc as _as_utc,
    _build_headless_client as _build_headless_client,
    _classify_dialog as _classify_dialog,
    _inside_window as _inside_window,
    _registry_scam_sources as _registry_scam_sources,
    channel_sources as channel_sources,
    discover_scam_channels as discover_scam_channels,
    sweep_scam_channels as sweep_scam_channels,
)
from rugmunchbot.channels.scam_watch._pipeline import (
    _MAX_SEEN_CACHE as _MAX_SEEN_CACHE,
    _SCAN_QUEUE_KEY as _SCAN_QUEUE_KEY,
    _SEEN_CACHE as _SEEN_CACHE,
    _SEEN_KEY as _SEEN_KEY,
    _collect_submissions as _collect_submissions,
    _enqueue_for_scanner as _enqueue_for_scanner,
    _feed_rag as _feed_rag,
    _feed_scanner as _feed_scanner,
    _mark_seen as _mark_seen,
    submit_for_scan as submit_for_scan,
)
from rugmunchbot.channels.scam_watch._runner import (
    run_scam_watch as run_scam_watch,
)

__all__ = [
    "SCAM_CHANNELS",
    "SCAM_WATCH_ENABLED",
    "SCAM_WATCH_ENQUEUE",
    "SCAM_WATCH_EXCLUDE",
    "SCAM_WATCH_INCLUDE_WATCHERS",
    "SCAM_WATCH_INTERVAL_MINUTES",
    "SCAM_WATCH_MAX_DISCOVERED",
    "SCAM_WATCH_PER_CHANNEL_LIMIT",
    "SCAM_WATCH_QUEUE_LIMIT",
    "SCAM_WATCH_SCAN_LIMIT",
    "SCAM_WATCH_SWEEP_HOURS",
    "ScamSourceType",
    "channel_sources",
    "classify_entity",
    "discover_scam_channels",
    "extract_token_addresses",
    "run_scam_watch",
    "submit_for_scan",
    "sweep_scam_channels",
]
