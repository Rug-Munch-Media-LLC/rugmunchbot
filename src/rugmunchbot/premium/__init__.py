#!/usr/bin/env python3
"""Paid-tier surfaces: gating, quota meter, forensic scan, alerts, scam feed, KOLs.

One package, one gate. ``gating`` is the only module that answers "may this
tier use this?"; everything else asks it. That is why the refusal copy is
identical across features and why a new surface cannot invent its own rule.

What each module owns:
  ``gating``        tier ranks, feature table, refusal card
  ``quota``         weekly meter, reset clock, upgrade path (``/premium``)
  ``deepscan_job``  progress + cancel + scanner module tiers
  ``watch_alerts``  quiet hours, per-user cap, dedupe
  ``scam_feed``     evidence cards naming the failing scanner checks
  ``kol_tracker``   declared KOL wallets, new-position alerts
"""

from __future__ import annotations

from rugmunchbot.premium._commands import (
    cmd_cancelscan,
    cmd_koltrack,
    cmd_premium,
    cmd_scamfeed,
    register_premium,
)
from rugmunchbot.premium.gating import (
    FEATURES,
    PREMIUM_RANK,
    RANK,
    allows,
    gate,
    rank_of,
    refusal,
)
from rugmunchbot.premium.quota import (
    Usage,
    next_reset,
    next_tier,
    read,
    render,
    snapshot,
)

__all__ = [
    "FEATURES",
    "PREMIUM_RANK",
    "RANK",
    "Usage",
    "allows",
    "cmd_cancelscan",
    "cmd_koltrack",
    "cmd_premium",
    "cmd_scamfeed",
    "gate",
    "next_reset",
    "next_tier",
    "rank_of",
    "read",
    "refusal",
    "register_premium",
    "render",
    "snapshot",
]
