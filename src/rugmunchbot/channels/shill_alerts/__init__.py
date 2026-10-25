"""Shill / scam-promotion detection across monitored Telegram channels.

Complements ``channels.shill_watch`` (cross-channel campaign counting) with a
scored, evidence-carrying rail: eight signals, a two-independent-signal gate,
a per-entity/day dedupe ledger, a review queue for single-heuristic items, and
a compact evidence card with moderation buttons.

Pipeline (``run_shill_alert_scan``):
    registry ─▶ sweep ─▶ signals ─▶ gate ─▶ main card (alerts channel)
                                     └───▶ review queue (never posted)

Rules that are enforced by tests, not by convention:
  * two independent signal families minimum — one family is one observation;
  * the same entity pushed by ten channels is ONE alert listing ten sources;
  * a restart replays sources, never re-posts;
  * quiet hours and a per-run cap both hold, and a held alert is retried.

Layout:
  ``config``   env switches        ``registry``  the ONE schema adapter
  ``signals``  pure extraction     ``gate``      the two-signal gate
  ``dedupe``   durable identity    ``card``      evidence card + buttons
  ``plan``     grouping            ``sweep``     bounded channel read
  ``deliver``  quiet hours + send  ``actions``   moderation button intents
"""

from __future__ import annotations

from rugmunchbot.channels.shill_alerts.actions import (
    VALID_ACTIONS as VALID_ACTIONS,
    handle_moderation_action as handle_moderation_action,
    resolve_action as resolve_action,
)
from rugmunchbot.channels.shill_alerts.card import (
    CALLBACK_PREFIX as CALLBACK_PREFIX,
    inline_markup as inline_markup,
    render_alert as render_alert,
    render_review_line as render_review_line,
)
from rugmunchbot.channels.shill_alerts.config import (
    ALERT_LEDGER as ALERT_LEDGER,
    CHANNEL_ROLE as CHANNEL_ROLE,
    ENABLED as ENABLED,
    MAX_PER_RUN as MAX_PER_RUN,
    MIN_CONFIDENCE as MIN_CONFIDENCE,
    MIN_INTERVAL_S as MIN_INTERVAL_S,
    QUIET_HOURS as QUIET_HOURS,
    REVIEW_LEDGER as REVIEW_LEDGER,
)
from rugmunchbot.channels.shill_alerts.dedupe import (
    AlertLedger as AlertLedger,
    AlertRecord as AlertRecord,
    alert_key as alert_key,
    observation_key as observation_key,
    today as today,
)
from rugmunchbot.channels.shill_alerts.deliver import (
    deliver_alerts as deliver_alerts,
    in_quiet_hours as in_quiet_hours,
)
from rugmunchbot.channels.shill_alerts.gate import (
    DROP as DROP,
    MAIN as MAIN,
    REVIEW as REVIEW,
    Verdict as Verdict,
    decide as decide,
    describe as describe,
)
from rugmunchbot.channels.shill_alerts.models import Alert as Alert
from rugmunchbot.channels.shill_alerts.plan import plan_alerts as plan_alerts
from rugmunchbot.channels.shill_alerts.registry import (
    Channel as Channel,
    load_registry as load_registry,
)
from rugmunchbot.channels.shill_alerts.runner import (
    run_shill_alert_scan as run_shill_alert_scan,
)
from rugmunchbot.channels.shill_alerts.signals import (
    MessageSignals as MessageSignals,
    Signal as Signal,
    extract_signals as extract_signals,
    find_entity as find_entity,
)
from rugmunchbot.channels.shill_alerts.sweep import sweep_channels as sweep_channels

__all__ = [
    "ALERT_LEDGER",
    "CALLBACK_PREFIX",
    "CHANNEL_ROLE",
    "DROP",
    "ENABLED",
    "MAIN",
    "MAX_PER_RUN",
    "MIN_CONFIDENCE",
    "MIN_INTERVAL_S",
    "QUIET_HOURS",
    "REVIEW",
    "REVIEW_LEDGER",
    "VALID_ACTIONS",
    "Alert",
    "AlertLedger",
    "AlertRecord",
    "Channel",
    "MessageSignals",
    "Signal",
    "Verdict",
    "alert_key",
    "decide",
    "deliver_alerts",
    "describe",
    "extract_signals",
    "find_entity",
    "handle_moderation_action",
    "in_quiet_hours",
    "inline_markup",
    "load_registry",
    "observation_key",
    "plan_alerts",
    "render_alert",
    "render_review_line",
    "resolve_action",
    "run_shill_alert_scan",
    "sweep_channels",
    "today",
]
