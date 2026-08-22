"""Maintenance + ban gates installed on the application."""

from __future__ import annotations

from datetime import time as dtime  # noqa: F401 — used by daily_scam_tip if re-enabled
from typing import TYPE_CHECKING, Any

from rugmunchbot import db
from rugmunchbot.states import is_owner
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


if TYPE_CHECKING:
    from telegram.ext import (
        Application,
    )


def install_maintenance_gate(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Globally disable the bot via /admin_toggle (maintenance mode).

    Wraps ``Application.process_update`` so every non-owner update is dropped
    (with a maintenance notice) while the bot is disabled. Reads the Redis
    shared state (admin_state.is_bot_enabled) so the backend admin API and
    the bot agree; fails open (bot enabled) when Redis is down.
    Owner updates always pass through.
    """
    from rugmunchbot import admin_state

    async def guarded(update: object) -> None:
        effective_user = getattr(update, "effective_user", None)
        if effective_user is not None and not is_owner(effective_user.id):
            try:
                enabled = await admin_state.is_bot_enabled()
            except Exception:
                enabled = True
            if not enabled:
                effective_message = getattr(update, "effective_message", None)
                callback_query = getattr(update, "callback_query", None)
                if effective_message is not None:
                    try:
                        await effective_message.reply_text(
                            "\U0001f527 <b>Rug Munch is under maintenance</b>\n\n"
                            "We're upgrading the intelligence pipeline \u2014 check back soon!\n"
                            "\U0001f6e1\ufe0f rugmunch.io",
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.debug("maintenance notice send failed", exc_info=True)
                elif callback_query is not None:
                    try:
                        await callback_query.answer("Bot is in maintenance mode.", show_alert=True)
                    except Exception:
                        logger.debug("maintenance callback answer failed", exc_info=True)
                return
        await original_process_update(update)

    original_process_update = app.process_update
    app.process_update = guarded  # type: ignore[method-assign]  # intentional monkeypatch


async def _send_ban_notice(update: object) -> None:
    """Send the single banned-user notice on whatever update type arrived."""
    effective_message = getattr(update, "effective_message", None)
    callback_query = getattr(update, "callback_query", None)
    inline_query = getattr(update, "inline_query", None)
    if effective_message is not None:
        try:
            await effective_message.reply_text(
                "\U0001f6ab <b>You are banned from using this bot.</b>",
                parse_mode="HTML",
            )
        except Exception:
            logger.debug("ban notice send failed", exc_info=True)
    elif callback_query is not None:
        try:
            await callback_query.answer("You are banned from using this bot.", show_alert=True)
        except Exception:
            logger.debug("ban callback answer failed", exc_info=True)
    elif inline_query is not None:
        try:
            await inline_query.answer([], cache_time=60, is_personal=True)
        except Exception:
            logger.debug("ban inline answer failed", exc_info=True)


def install_ban_gate(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Block banned users from EVERY command, callback, inline and message.

    Wraps ``Application.process_update`` so the ban check runs before any
    handler is dispatched — commands, callback queries, inline queries,
    pre-checkout and free text all pass through this single gate.

    Consults BOTH authoritative stores:
      * local SQLite ``users.is_banned`` (written by /admin_ban, /admin_unban
        and the admin bot),
      * the Redis set ``bot:admin:banned_users`` (written by the backend admin
        API via ``admin_state.ban_user``).
    Fails open (allows) when a store is unavailable. Owners are always exempt
    so they can still reach /admin_unban. Banned users receive a single
    "You are banned" notice and the update is dropped — no command executes.
    """
    from rugmunchbot import admin_state

    async def guarded(update: object) -> None:
        effective_user = getattr(update, "effective_user", None)
        if effective_user is not None and not is_owner(effective_user.id):
            banned = False
            try:
                banned = await admin_state.is_user_banned(effective_user.id)
            except Exception:
                logger.debug("admin_state:is_user_banned failed", exc_info=True)
            if not banned:
                try:
                    banned = db.is_user_banned(effective_user.id)
                except Exception:
                    logger.debug("db:is_user_banned failed", exc_info=True)
            if banned:
                logger.info("ban-gate blocked user=%s", effective_user.id)
                await _send_ban_notice(update)
                return
        await original_process_update(update)

    original_process_update = app.process_update
    app.process_update = guarded  # type: ignore[method-assign]  # intentional monkeypatch
