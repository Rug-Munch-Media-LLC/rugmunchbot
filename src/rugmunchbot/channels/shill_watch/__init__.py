"""Coordinated shill-campaign detection — same token pushed by many accounts.

Modularized from the former single-module ``channels/shill_watch.py``
(2026-09-03). This ``__init__`` is a pure re-export shim: every public name
and signature is unchanged, and callers keep importing
``channels.shill_watch`` exactly as before.

Channel-quality overhaul (2026-08-29), deliverable 4: a shill campaign is a
token (address or ``$TICKER``) that many distinct accounts / KOL call
channels push inside a short window. Legit news coverage is typically a
couple of outlets; a coordinated campaign is N distinct pushers in W hours
— that is the signal, and it belongs on the ALERTS channel
(@rmialerts, high-signal only — alongside exploit + scam/rug detections).

2026-08-30 (social ingest airtight): shills delete tweets, so a text-only
rail loses the evidence. When a campaign is detected we now:
  * capture a cheap screenshot of the X post (``tools.shill_shot`` — one
    DeepInfra VL-4B vision call per shill, bounded),
  * save the screenshot reference + extracted OCR text to the DB and to the
    RAG ``threat_intel_v2`` Qdrant collection (with the source link),
  * rank the campaign by rerank importance (same qwen3-reranker lane as the
    news feed) and route: ``>= main gate`` → @cryptorugmuncher (+ @rmialerts),
    ``>= news floor`` → @rmialerts, below → hold. A coordinated shill is
    danger intel, so it never lands on the news channel.

Pipeline:
  1. ``run_shill_watch`` sweeps the focused KOL/alpha channel list
     (``SHILL_WATCH_CHANNELS`` — bounded, ~11 channels, NOT the full
     200-channel news sweep) for the last ``SHILL_SWEEP_HOURS``.
  2. ``detect_shill_campaigns`` (pure) groups articles by token identity,
     counts DISTINCT pushing channels per token inside the window, and
     flags tokens with >= ``SHILL_MIN_ACCOUNTS`` distinct pushers and
     >= ``SHILL_MIN_MENTIONS`` total mentions.
  3. ``run_shill_watch`` scores each campaign via the reranker
     (``channels.importance.score_importance``), captures X screenshots,
     runs address-token campaigns through the canonical scanner
     (``build_scanner_evidence`` — honeypot/deployer/bundled-supply/LP
     evidence for the "why this is likely a scam" note), persists evidence,
     and posts the routed alert (alerts / alerts+main, held below the floor).

Fail-open everywhere: a dead sweep client, empty sweep, missing X URL,
unreachable vision lane, scanner outage, Redis outage, or DB write failure
never crashes the tick — silence is a valid outcome.

Layout:
  * ``_config.py``   — env switches, channel registry, Qdrant endpoint
  * ``_detect.py``   — ShillCampaign + pure detection + URL helpers
  * ``_evidence.py`` — DB/Qdrant persistence + screenshot/OCR capture
  * ``_scoring.py``  — rerank scoring + scanner evidence + alert formatting
  * ``_runner.py``   — focused sweep + run_shill_watch entry point
"""

from __future__ import annotations

from rugmunchbot.channels.shill_watch._config import (
    SHILL_INTERVAL_MIN as SHILL_INTERVAL_MIN,
    SHILL_MAX_ALERTS as SHILL_MAX_ALERTS,
    SHILL_MIN_ACCOUNTS as SHILL_MIN_ACCOUNTS,
    SHILL_MIN_MENTIONS as SHILL_MIN_MENTIONS,
    SHILL_SCREENSHOT_BUDGET as SHILL_SCREENSHOT_BUDGET,
    SHILL_SCREENSHOT_DIR as SHILL_SCREENSHOT_DIR,
    SHILL_SCREENSHOT_ENABLED as SHILL_SCREENSHOT_ENABLED,
    SHILL_SCREENSHOT_PER_CAMPAIGN as SHILL_SCREENSHOT_PER_CAMPAIGN,
    SHILL_SWEEP_HOURS as SHILL_SWEEP_HOURS,
    SHILL_WATCH_CHANNELS as SHILL_WATCH_CHANNELS,
    SHILL_WATCH_ENABLED as SHILL_WATCH_ENABLED,
    SHILL_WINDOW_HOURS as SHILL_WINDOW_HOURS,
    TG_CAPTURE_BUDGET as TG_CAPTURE_BUDGET,
    TG_CAPTURE_ENABLED as TG_CAPTURE_ENABLED,
    logger as logger,
)
from rugmunchbot.channels.shill_watch._detect import (
    ShillCampaign as ShillCampaign,
    _article_fields as _article_fields,
    _esc as _esc,
    _posted_at as _posted_at,
    _tg_post_url as _tg_post_url,
    _token_ids as _token_ids,
    _x_status_url as _x_status_url,
    detect_shill_campaigns as detect_shill_campaigns,
)
from rugmunchbot.channels.shill_watch._evidence import (
    _capture_campaign_shills as _capture_campaign_shills,
    _ensure_shill_table as _ensure_shill_table,
    _persist_shill_evidence as _persist_shill_evidence,
    capture_telegram_kol_posts as capture_telegram_kol_posts,
    capture_telegram_message_screenshot as capture_telegram_message_screenshot,
    capture_tweet_screenshot as capture_tweet_screenshot,
)
from rugmunchbot.channels.shill_watch._runner import (
    _article_to_dict as _article_to_dict,
    _campaign_from_dict as _campaign_from_dict,
    _detect_via_backend as _detect_via_backend,
    _sweep_shill_sources as _sweep_shill_sources,
    run_shill_watch as run_shill_watch,
)
from rugmunchbot.channels.shill_watch._scoring import (
    SHILL_SCANNER_EVIDENCE_ENABLED as SHILL_SCANNER_EVIDENCE_ENABLED,
    _evidence_bullets as _evidence_bullets,
    _looks_like_address as _looks_like_address,
    _pct as _pct,
    _score_shill_campaign as _score_shill_campaign,
    build_scanner_evidence as build_scanner_evidence,
    format_shill_alert as format_shill_alert,
)

__all__ = [
    "SHILL_INTERVAL_MIN",
    "SHILL_MAX_ALERTS",
    "SHILL_MIN_ACCOUNTS",
    "SHILL_MIN_MENTIONS",
    "SHILL_SCANNER_EVIDENCE_ENABLED",
    "SHILL_SCREENSHOT_BUDGET",
    "SHILL_SCREENSHOT_DIR",
    "SHILL_SCREENSHOT_ENABLED",
    "SHILL_SCREENSHOT_PER_CAMPAIGN",
    "SHILL_SWEEP_HOURS",
    "SHILL_WATCH_CHANNELS",
    "SHILL_WATCH_ENABLED",
    "SHILL_WINDOW_HOURS",
    "TG_CAPTURE_BUDGET",
    "TG_CAPTURE_ENABLED",
    "ShillCampaign",
    "build_scanner_evidence",
    "capture_telegram_kol_posts",
    "capture_telegram_message_screenshot",
    "capture_tweet_screenshot",
    "detect_shill_campaigns",
    "format_shill_alert",
    "run_shill_watch",
]
