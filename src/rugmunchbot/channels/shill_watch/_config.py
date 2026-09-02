"""Configuration for the shill-watch channel (env switches + channel registry).

Split out of ``channels/shill_watch.py`` during the de-godding pass. Holds
the logger, every ``RMI_SHILL_*`` env switch, the focused KOL/alpha channel
registry, and the Qdrant evidence endpoint. Pure module: no network, no I/O.
"""

from __future__ import annotations

import os
import tempfile

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.shill_watch")

SHILL_WATCH_ENABLED = os.getenv("RMI_SHILL_WATCH_ENABLED", "true").lower() in ("1", "true", "yes")
SHILL_INTERVAL_MIN = int(os.getenv("RMI_SHILL_WATCH_INTERVAL_MIN", "60"))
#: Bounded sweep window (hours) — shill campaigns are fast; no need to look
#: back a week. Must stay <= the news sweep window for consistent freshness.
SHILL_SWEEP_HOURS = int(os.getenv("RMI_SHILL_WATCH_SWEEP_HOURS", "6"))
#: Messages read per focused channel per sweep (bounded).
SHILL_PER_CHANNEL_LIMIT = int(os.getenv("RMI_SHILL_WATCH_PER_CHANNEL_LIMIT", "30"))
#: Distinct pushing accounts that make a token a campaign.
SHILL_MIN_ACCOUNTS = int(os.getenv("RMI_SHILL_MIN_ACCOUNTS", "3"))
#: Minimum total mentions (articles) across those accounts.
SHILL_MIN_MENTIONS = int(os.getenv("RMI_SHILL_MIN_MENTIONS", "3"))
#: Max alerts posted per run (bounded, no spam).
SHILL_MAX_ALERTS = int(os.getenv("RMI_SHILL_MAX_ALERTS", "3"))
#: Detection window for grouping (hours).
SHILL_WINDOW_HOURS = int(os.getenv("RMI_SHILL_WINDOW_HOURS", "24"))

#: 2026-08-30: campaign detection is now served by the BACKEND
#: (``/api/v1/shill/campaigns/detect``). ``RMI_SHILL_USE_BACKEND`` gates
#: whether the bot is a thin consumer (default) or falls back to the local
#: pure detector. The local detector stays as an offline/degraded rail.
SHILL_USE_BACKEND = os.getenv("RMI_SHILL_USE_BACKEND", "true").lower() in ("1", "true", "yes")

#: 2026-08-30 screenshot capture knobs. A shill deletes its tweet, so the
#: Wall of Shame attaches evidence. The capture is one DeepInfra VL-4B vision
#: call per shill mention and is strictly bounded so a busy campaign can
#: never burn the whole budget. ``RMI_SHILL_SCREENSHOT_ENABLED`` gates it.
SHILL_SCREENSHOT_ENABLED = os.getenv("RMI_SHILL_SCREENSHOT_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
#: Directory for PNG evidence (defaults to a writable generic temp dir —
#: never an internal fleet path, per the methodology-leak policy).
SHILL_SCREENSHOT_DIR = os.getenv(
    "RMI_SHILL_SCREENSHOT_DIR",
    os.path.join(tempfile.gettempdir(), "rmi", "shill_shots"),
)
#: Max screenshots captured per run (across all campaigns).
SHILL_SCREENSHOT_BUDGET = int(os.getenv("RMI_SHILL_SCREENSHOT_BUDGET", "6"))
#: Max screenshots per campaign (one vision call per shill, capped).
SHILL_SCREENSHOT_PER_CAMPAIGN = int(os.getenv("RMI_SHILL_SCREENSHOT_PER_CAMPAIGN", "3"))
#: Max total OCR text chars persisted per screenshot (bounded payload).
_SHILL_OCR_MAX_CHARS = 1200
#: 2026-09-01 Telegram KOL capture: monitored KOL channel/group posts are
#: captured through the SAME screenshot+OCR pipeline (public ``t.me/s/``
#: web preview via ``tools.shill_shot``) and persisted with
#: ``source='telegram'`` so the site can distinguish origin.
TG_CAPTURE_ENABLED = os.getenv("RMI_SHILL_TG_CAPTURE_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
#: Max Telegram post captures per run (bounded — one vision call each).
TG_CAPTURE_BUDGET = int(os.getenv("RMI_SHILL_TG_CAPTURE_BUDGET", "4"))
#: Canonical Qdrant URL + collection for shill/screenshot evidence. The
#: logical-collection resolver (``_vendor.rag_pkg._qdrant``) can redirect a
#: request for ``threat_intel_v2`` to ``rag_documents`` when the existence
#: probe is cached/missed, so we upsert DIRECTLY to the canonical endpoint
#: (``RMI_QDRANT_URL`` or the fleet ``QDRANT_URL`` chain) with the physical
#: collection name. 2026-09-01: default is ``rmi-qdrant-v2:6333`` (canonical)
#: — the old ``127.0.0.1:6334`` is unreachable in-container.
_QDRANT_URL = os.getenv("RMI_QDRANT_URL") or os.getenv(
    "QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://rmi-qdrant-v2:6333")
).rstrip("/")
_SHILL_EVIDENCE_COLLECTION = os.getenv("RMI_SHILL_EVIDENCE_COLLECTION", "threat_intel_v2")

#: Focused KOL/alpha/call-analysis channels the shill watcher sweeps.
#: Comma-separated env override (``RMI_SHILL_WATCH_CHANNELS``). Deliberately
#: small — this is the coordinated-pusher signal set, not the news feed.
_DEFAULT_SHILL_CHANNELS: tuple[str, ...] = (
    "CallAnalyser",  # KOL call analysis
    "walloftrophies",  # KOL Trending / CallAnalyser
    "thepumpalert",  # scam call-out channel
    "kamocalls",  # Kamo | Daily trades
    "CryptoCapoTG",  # CryptoCapo — calls + analysis
    "shahlito",  # Shah's Journal — research/calls
    "skeletonlounge",  # Skeleton Lounge Portal — calls
    "rickburpbotnews",  # Rick's Lab — research/alpha
    "dimethyeth",  # dime — research
    "OnChainWizard",  # OnChain Wizard — alpha
    "cryptowhaletrades",  # Crypto Whale Trading — whale/calls
)
SHILL_WATCH_CHANNELS: tuple[str, ...] = tuple(
    c.strip().lstrip("@")
    for c in os.getenv("RMI_SHILL_WATCH_CHANNELS", ",".join(_DEFAULT_SHILL_CHANNELS)).split(",")
    if c.strip()
)
