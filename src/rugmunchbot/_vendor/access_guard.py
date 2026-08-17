# VENDORED 2026-08-17 from rmi-backend/app/domains/telegram/access_guard.py (Phase 2a — frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
#!/usr/bin/env python3
"""
access_guard — shared non-owner attempt guard for the Telegram bots.

Defense-in-depth layer: every time a non-owner triggers an owner-only
surface (admin command, admin callback, owner keyboard, group admin
command), we log a WARNING fingerprint and send a RATE-LIMITED alert to
the owner's private chat so probing is visible without enabling an alert
flood (max one alert per user+surface per 10 minutes).

Design rules:
  * Fail-open — never raises, never blocks the deny path. The caller
    already denied the update; this only records + notifies.
  * Rate-limited — TTLCache keyed (user_id, surface), so a botnet of
    new users still alerts once each but one attacker spamming the same
    command cannot flood the owner chat.
  * Both bots use it: rugmunchbot (public, replies "owner only" to
    non-owners) and rmiadminbot (hidden, silent deny). The alert body is
    shared so both land in the same format.
"""

from __future__ import annotations

import logging
from typing import Any

from cachetools import TTLCache

logger = logging.getLogger("telegram.access_guard")

# Alert at most once per (user_id, surface) per 10 minutes.
_ALERT_TTL_SECONDS = 600
_ALERT_CACHE_MAXSIZE = 4096
_alerted: TTLCache[tuple[int | None, str], bool] = TTLCache(maxsize=_ALERT_CACHE_MAXSIZE, ttl=_ALERT_TTL_SECONDS)


def _fmt_user(uid: int | None, username: str | None) -> str:
    if username:
        return f"@{username} (id {uid})" if uid else f"@{username}"
    return f"id {uid}" if uid else "unknown"


async def deny_admin_attempt(
    bot: Any,
    owner_chat_id: int,
    *,
    bot_name: str,
    surface: str,
    user_id: int | None = None,
    username: str | None = None,
    chat_id: int | None = None,
    chat_type: str | None = None,
    audit: Any = None,
    audit_detail: str = "",
) -> None:
    """Log + rate-limited owner alert for a denied non-owner attempt.

    Args:
        bot: telegram Bot (or anything with async send_message).
        owner_chat_id: chat to alert (owner's private chat).
        bot_name: "rugmunchbot" or "rmiadminbot" (alert attribution).
        surface: what was attempted, e.g. "cmd:/admin_ban", "cb:admin_stats".
        user_id / username: attacker identity (best effort).
        chat_id / chat_type: where the attempt happened.
        audit: optional sync callable(uid, action, detail) for the local
            audit trail (fail-open, wrapped).
        audit_detail: extra detail passed to audit.
    """
    who = _fmt_user(user_id, username)
    where = f" chat={chat_id} ({chat_type or '?'})" if chat_id else ""
    line = f"{bot_name} deny surface={surface} user={who}{where}"

    logger.warning("ACCESS_DENY %s", line)

    # Local audit trail (fail-open, never blocks).
    if audit is not None:
        try:
            if user_id is not None:
                audit(user_id, f"deny:{surface}", audit_detail or line)
        except Exception:
            logger.debug("access_guard: audit failed", exc_info=True)

    if not owner_chat_id:
        return

    # Rate-limit: first attempt per (user, surface) alerts; repeats are
    # still logged above but do not spam the owner.
    key = (user_id, surface)
    if key in _alerted:
        return
    _alerted[key] = True

    try:
        await bot.send_message(
            chat_id=owner_chat_id,
            text=(
                "\u26a0\ufe0f <b>Admin attempt denied</b>\n"
                f"<b>Bot:</b> {bot_name}\n"
                f"<b>User:</b> {who}\n"
                f"<b>Surface:</b> <code>{surface}</code>\n"
                f"<b>Chat:</b> {chat_id} ({chat_type or '?'})"
            ),
            parse_mode="HTML",
        )
    except Exception:
        logger.debug("access_guard: owner alert send failed", exc_info=True)


def reset_alert_cache() -> None:
    """Clear the rate-limit cache (tests / manual debugging only)."""
    _alerted.clear()
