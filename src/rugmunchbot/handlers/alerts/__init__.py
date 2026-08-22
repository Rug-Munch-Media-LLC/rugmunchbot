"""RugMunchBot -- Alert engine + /alert and /alerts commands (package).

Modularized from the former single-module ``handlers/alerts.py`` (2026-08-22).
This ``__init__`` is a pure re-export shim: every public name and signature is
unchanged, and callers keep importing ``handlers.alerts`` exactly as before.

Background price alert checker, paid-token monitor re-scan delta job, backend
webhook subscriptions, and the alert management commands.

Layout:
  * ``_constants.py``    — direction labels, event types, job budgets
  * ``_conditions.py``   — pure trigger conditions + monitor security deltas
  * ``_prices.py``       — own-data-first batch price fetching
  * ``_backend_subs.py`` — fail-open webhook bridge subscriptions
  * ``_jobs.py``         — check_alerts / check_monitors JobQueue entry points
  * ``_commands.py``     — cmd_alerts / cmd_alert handlers
"""

from __future__ import annotations

from rugmunchbot.handlers.alerts._backend_subs import (
    _subscribe_backend_alerts as _subscribe_backend_alerts,
    _subscribe_backend_monitor as _subscribe_backend_monitor,
    _unsubscribe_backend_monitor as _unsubscribe_backend_monitor,
)
from rugmunchbot.handlers.alerts._commands import (
    cmd_alert as cmd_alert,
    cmd_alerts as cmd_alerts,
)
from rugmunchbot.handlers.alerts._conditions import (
    _check_alert_condition as _check_alert_condition,
    _monitor_baseline as _monitor_baseline,
    _monitor_deltas as _monitor_deltas,
    _pct as _pct,
)
from rugmunchbot.handlers.alerts._constants import (
    ALERT_EVENT_TYPES as ALERT_EVENT_TYPES,
    DIRECTION_LABELS as DIRECTION_LABELS,
    MONITOR_CHECK_INTERVAL as MONITOR_CHECK_INTERVAL,
    MONITOR_EVENT_TYPES as MONITOR_EVENT_TYPES,
    MONITOR_SCAN_BUDGET as MONITOR_SCAN_BUDGET,
)
from rugmunchbot.handlers.alerts._jobs import (
    check_alerts as check_alerts,
    check_monitors as check_monitors,
)
from rugmunchbot.handlers.alerts._prices import (
    _fetch_prices_batch as _fetch_prices_batch,
)

__all__ = [
    "ALERT_EVENT_TYPES",
    "DIRECTION_LABELS",
    "MONITOR_CHECK_INTERVAL",
    "MONITOR_EVENT_TYPES",
    "MONITOR_SCAN_BUDGET",
    "check_alerts",
    "check_monitors",
    "cmd_alert",
    "cmd_alerts",
]
