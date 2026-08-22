"""Alert engine constants — direction labels, event types, job budgets.

Extracted verbatim from the former single-module ``handlers/alerts.py``.
"""

from __future__ import annotations

DIRECTION_LABELS = {
    "above": "\U0001f4c8 above",
    "below": "\U0001f4c9 below",
    "change_5": "\U0001f4ca 5% change",
    "any": "\U0001f504 significant change",
}

# Real-time event types a user alert subscribes to (delivered as push alerts
# through the intelligent webhook bridge: provider → engine → Redis queue →
# bot deliver_pending_alerts job → Telegram DM).
ALERT_EVENT_TYPES = [
    "token.price_change",
    "token.whale_move",
    "token.liquidity_change",
]

# Event types the Stars-paid Monitor subscribes to. These are the on-chain
# signals the webhook engine emits per token (provider → engine → Redis queue
# → deliver_pending_alerts → DM): LP add/remove (``token.liquidity_change``),
# large/deployer wallet moves (``token.whale_move``) and price swings
# (``token.price_change``). Tax/ownership/mint changes are covered by the
# periodic ``check_monitors`` re-scan delta job (those properties only change
# on-chain, and the scanner cache surfaces them). ``transaction.enhanced``
# (Helius-style) is intentionally NOT subscribed here: the engine routes it by
# wallet_address, and the bot bridge only filters by token.
MONITOR_EVENT_TYPES = [
    "token.liquidity_change",
    "token.whale_move",
    "token.price_change",
]

# How often the tax/ownership/mint delta job re-scans active monitors.
MONITOR_CHECK_INTERVAL = 1800  # seconds (30 min)
# Hard cap on monitors re-scanned per tick so a paid-user burst never stalls
# the JobQueue (unscanned monitors are picked up on the next tick).
MONITOR_SCAN_BUDGET = 25
