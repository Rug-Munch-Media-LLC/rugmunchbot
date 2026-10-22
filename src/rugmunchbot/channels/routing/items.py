"""Route a batch of detector items through the channel routing table.

The posters collect heterogeneous dicts (scanner risk alerts, alpha sections).
This module derives the routing category and severity for one item and keeps
only what :func:`rugmunchbot.channels.routing.gate.decide` allows, so a poster
never needs its own severity floor.

Severity comes from the scanner risk score when present (``risk_score`` /
``risk`` / ``score``), else from an explicit ``severity`` key; anything below
0.75 is below the @rmialerts floor by design — a routine risk sweep is not a
real-time security alert.
"""

from __future__ import annotations

import logging
from typing import Any

from rugmunchbot.channels.routing.gate import decide

logger = logging.getLogger("rmi_bot.channels.routing.items")

#: Risk-flag substring -> security category (first match wins).
FLAG_CATEGORY: tuple[tuple[str, str], ...] = (
    ("honeypot", "honeypot"),
    ("exploit", "exploit"),
    ("rug", "rug_pull"),
    ("blacklist", "blacklist"),
    ("mint", "mint"),
    ("liquidity", "liquidity_remove"),
    ("deployer", "deployer_sell"),
    ("shill", "shill"),
    ("scam", "scam"),
)

SEVERITY_BANDS: tuple[tuple[float, str], ...] = (
    (0.90, "critical"),
    (0.75, "high"),
    (0.50, "medium"),
)


def severity_of(item: dict[str, Any]) -> str:
    """Severity for one item: explicit key, else bucketed risk score."""
    explicit = str(item.get("severity") or "").strip().lower()
    if explicit in ("low", "medium", "high", "critical"):
        return explicit
    raw = item.get("risk_score", item.get("risk", item.get("score", 0.0)))
    try:
        score = float(raw)
    except (TypeError, ValueError):
        score = 0.0
    if score > 1.0:  # 0-100 scale
        score /= 100.0
    for floor, name in SEVERITY_BANDS:
        if score >= floor:
            return name
    return "low"


def category_of(item: dict[str, Any]) -> str:
    """Security category for one item, from its risk flags (or ``alert``)."""
    explicit = str(item.get("category") or "").strip().lower()
    if explicit:
        return explicit
    flags = " ".join(str(f).lower() for f in (item.get("risk_flags") or item.get("threats") or []))
    blob = f"{flags} {str(item.get('alert_type') or '').lower()}"
    for needle, category in FLAG_CATEGORY:
        if needle in blob:
            return category
    return "alert"


def filter_for_channel(channel: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the items *channel* may carry; log every suppression reason.

    Preserves input order. Rate budget is consumed per kept item, so a batch
    larger than the channel's hourly cap is truncated rather than burst-sent.
    """
    kept: list[dict[str, Any]] = []
    for item in items:
        decision = decide(channel, category_of(item), severity_of(item))
        if decision.allowed:
            kept.append(item)
        else:
            logger.info(
                "channels:routing_suppressed",
                channel=channel,
                category=decision.category,
                severity=decision.severity,
                reason=decision.reason,
                redirect=decision.redirect,
            )
    return kept
