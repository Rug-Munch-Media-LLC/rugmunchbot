#!/usr/bin/env python3
"""
RugMunchBot -- Owner/admin command handlers (v5 admin panel) — package.

Modularized from the former single-module ``handlers/owner.py`` (2026-08-22).
This ``__init__`` is a pure re-export shim: every public name and signature is
unchanged, and callers keep importing ``handlers.owner`` exactly as before.

Owner-only commands:
  /admin                  -> admin dashboard with bot stats + actions
  /admin_stats            -> users, active today, messages, RAG, AI tokens
  /credits                -> OpenRouter credit balance (usage / limit / free tier)
  /users                  -> user count, top users, DAU
  /admin_broadcast <msg>  -> broadcast to all users (with confirmation)
  /admin_toggle [on|off]  -> disable/enable the bot globally
  /admin_toggle_chat [on|off] -> disable/enable AI chat (commands keep working)
  /admin_user <user_id>   -> user details, tier, usage
  /admin_set_tier <uid> <tier> [days] -> change user tier
  /admin_ban <user_id>    -> ban user
  /admin_unban <user_id>  -> unban user
  /admin_logs             -> recent admin actions

Every action is written to the admin audit log (db.log_admin_action).
All commands are owner-gated (``is_owner``) and fail closed when
``TELEGRAM_ALLOWED_USERS`` is unset (no hardcoded owner default).

Layout:
  * ``_shared.py``    — audit log, deny gate, usage + credit blocks, state
  * ``_dashboard.py`` — /admin, /admin_stats, /credits, /users
  * ``_broadcast.py`` — broadcast preview/confirm/fan-out flow
  * ``_users.py``     — toggles, user lookup, tiers, bans, logs
"""

from __future__ import annotations

from rugmunchbot.handlers.owner._broadcast import (
    _do_broadcast as _do_broadcast,
    cb_broadcast_confirm as cb_broadcast_confirm,
    cmd_admin_broadcast as cmd_admin_broadcast,
    pending_broadcast as pending_broadcast,
)
from rugmunchbot.handlers.owner._dashboard import (
    cmd_admin as cmd_admin,
    cmd_admin_credits as cmd_admin_credits,
    cmd_admin_stats as cmd_admin_stats,
    cmd_admin_users as cmd_admin_users,
)
from rugmunchbot.handlers.owner._rpcstats import (
    cmd_rpcstats as cmd_rpcstats,
)
from rugmunchbot.handlers.owner._shared import (
    _deny_admin_attempt as _deny_admin_attempt,
    _fetch_credit_block as _fetch_credit_block,
    _format_model_usage_line as _format_model_usage_line,
    _log as _log,
    _usage_line as _usage_line,
)
from rugmunchbot.handlers.owner._users import (
    cmd_admin_ban as cmd_admin_ban,
    cmd_admin_logs as cmd_admin_logs,
    cmd_admin_set_tier as cmd_admin_set_tier,
    cmd_admin_toggle as cmd_admin_toggle,
    cmd_admin_toggle_chat as cmd_admin_toggle_chat,
    cmd_admin_unban as cmd_admin_unban,
    cmd_admin_user as cmd_admin_user,
)

__all__ = [
    "cb_broadcast_confirm",
    "cmd_admin",
    "cmd_admin_ban",
    "cmd_admin_broadcast",
    "cmd_admin_credits",
    "cmd_admin_logs",
    "cmd_admin_set_tier",
    "cmd_admin_stats",
    "cmd_admin_toggle",
    "cmd_admin_toggle_chat",
    "cmd_admin_unban",
    "cmd_admin_user",
    "cmd_admin_users",
    "cmd_rpcstats",
    "pending_broadcast",
]
