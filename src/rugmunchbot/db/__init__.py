#!/usr/bin/env python3
"""
CryptoRugMunch Bot - Database Layer
====================================
SQLite-based user tracking, subscriptions, scan counts, watchlists.

Thin re-export shim: the implementation lives in the ``db`` package
submodules (``_base``, ``users``, ``tiers``, ``watchlist``, ``stars``,
``alerts``). Every public function and module-level constant from the old
single-file ``db.py`` is re-exported here so existing importers
(``from app.domains.telegram.rugmunchbot import db``) resolve unchanged.
"""

from __future__ import annotations

from ._base import _resolve_db_path, get_db, init_db, logger
from .alerts import get_active_alerts, mark_alert_triggered
from .monitors import (
    MONITOR_DEFAULT_DAYS,
    add_monitor,
    expire_monitors,
    get_active_monitors,
    get_monitors,
    remove_monitor,
    update_monitor_baseline,
)
from .stars import add_stars, apply_top_up, spend_stars
from .tiers import (
    _current_week_start,
    activate_tier,
    check_rate_limit,
    get_user_tier,
    get_weekly_usage,
    increment_usage,
    set_user_tier,
)
from .users import (
    API_TOKEN_PREFIX_LEN,
    CHAT_HISTORY_CONTEXT,
    CHAT_HISTORY_KEEP,
    _is_hashed_token,
    _today_str,
    add_chat_message,
    clear_chat_history,
    count_active_today,
    get_admin_logs,
    get_bot_state,
    get_chat_history,
    get_daily_stats,
    get_known_scam_addresses,
    get_or_create_user,
    get_user_by_id,
    get_user_stats,
    hash_api_token,
    is_user_banned,
    list_scam_reports,
    log_admin_action,
    log_scan,
    migrate_plaintext_api_tokens,
    record_activity,
    report_scam,
    set_banned,
    set_bot_state,
    token_prefix,
    verify_api_token,
)
from .watchlist import (
    add_watchlist,
    get_watchlist,
    get_watchlist_for_user,
    remove_watchlist,
)

# Mutable DB path — owned by this shim so tests can keep patching it via
# ``monkeypatch.setattr(db, "DB_PATH", ...)``; ``_base.get_db()`` reads it
# from this namespace at call time.
DB_PATH = _resolve_db_path()

__all__ = [
    "API_TOKEN_PREFIX_LEN",
    "CHAT_HISTORY_CONTEXT",
    "CHAT_HISTORY_KEEP",
    "DB_PATH",
    "MONITOR_DEFAULT_DAYS",
    "_current_week_start",
    "_is_hashed_token",
    "_resolve_db_path",
    "_today_str",
    "activate_tier",
    "add_chat_message",
    "add_monitor",
    "add_stars",
    "add_watchlist",
    "apply_top_up",
    "check_rate_limit",
    "clear_chat_history",
    "count_active_today",
    "expire_monitors",
    "get_active_alerts",
    "get_active_monitors",
    "get_admin_logs",
    "get_bot_state",
    "get_chat_history",
    "get_daily_stats",
    "get_db",
    "get_known_scam_addresses",
    "get_monitors",
    "get_or_create_user",
    "get_user_by_id",
    "get_user_stats",
    "get_user_tier",
    "get_watchlist",
    "get_watchlist_for_user",
    "get_weekly_usage",
    "hash_api_token",
    "increment_usage",
    "init_db",
    "is_user_banned",
    "list_scam_reports",
    "log_admin_action",
    "log_scan",
    "logger",
    "mark_alert_triggered",
    "migrate_plaintext_api_tokens",
    "record_activity",
    "remove_monitor",
    "remove_watchlist",
    "report_scam",
    "set_banned",
    "set_bot_state",
    "set_user_tier",
    "spend_stars",
    "token_prefix",
    "update_monitor_baseline",
    "verify_api_token",
]

# Init on import
try:
    init_db()
except Exception:
    # Router must still mount if the DB is unavailable (e.g. read-only
    # filesystem) — handlers degrade gracefully per-request.
    logger.warning("bot_db_init_failed", exc_info=True)
