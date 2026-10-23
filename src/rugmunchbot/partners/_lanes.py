"""Content lane → partner venue mapping — the only lane registry.

Six content lanes are featured: news, alpha, whales, predictions,
scam_alert, smart_money. Order inside a lane is priority: the render cap
(default 3 links) truncates, so the first entries win.
"""

from __future__ import annotations

NEWS = "news"
ALPHA = "alpha"
WHALES = "whales"
PREDICTIONS = "predictions"
SCAM_ALERT = "scam_alert"
SMART_MONEY = "smart_money"

#: Every lane a partner strip is attached to.
PARTNER_LANES: tuple[str, ...] = (
    NEWS,
    ALPHA,
    WHALES,
    PREDICTIONS,
    SCAM_ALERT,
    SMART_MONEY,
)

#: lane → partner keys, highest priority first (keys resolve in _catalog).
LANE_PARTNERS: dict[str, tuple[str, ...]] = {
    NEWS: ("jupiter", "gmgn", "banana", "padre", "axiom", "odin"),
    ALPHA: ("gmgn", "axiom", "banana", "padre", "jupiter", "odin"),
    WHALES: ("hyperliquid", "gmgn", "banana", "jupiter", "padre"),
    PREDICTIONS: ("polymarket",),
    SCAM_ALERT: ("gmgn", "jupiter", "banana", "padre"),
    SMART_MONEY: ("gmgn", "axiom", "banana", "padre", "jupiter", "odin"),
}

#: ``content_type`` handed to ``_safe_send`` → content lane.
CONTENT_TYPE_LANES: dict[str, str] = {
    "news": NEWS,
    "alpha": ALPHA,
    "whale": WHALES,
    "predictions": PREDICTIONS,
    "scam_alert": SCAM_ALERT,
    "scan": SMART_MONEY,
}

#: ``channels.automation`` lane → content lane.
AUTOMATION_LANES: dict[str, str] = {
    "move": SMART_MONEY,
    "liquidation": WHALES,
    "odds": PREDICTIONS,
    "call": ALPHA,
}


def lane_for_content_type(content_type: str | None) -> str:
    """Resolve a ``content_type`` to a partner lane ("" when unfunded)."""
    return CONTENT_TYPE_LANES.get((content_type or "").strip().lower(), "")


def normalize_lane(lane: str | None) -> str:
    """Accept a lane name or a content type; return a funded lane or ""."""
    key = (lane or "").strip().lower().replace("-", "_")
    if key in LANE_PARTNERS:
        return key
    return lane_for_content_type(key)
