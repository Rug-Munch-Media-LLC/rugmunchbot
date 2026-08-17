"""Owner-only admin callbacks for the RugMunch bot.

Extracted from ``callback.py`` during the Batch 11 de-godding pass: admin_*
callback dispatch, stats/log cards and the shared non-owner deny helper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram import access_guard
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import OWNER_IDS, TIERS
from app.domains.telegram.rugmunchbot.formatters import is_owner, thin_sep
from app.domains.telegram.rugmunchbot.keyboards import admin_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def _deny_callback_attempt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, surface: str) -> None:
    """Log + alert a non-owner admin callback attempt (defense-in-depth)."""
    query = update.callback_query
    user = query.from_user if query else None
    chat = update.effective_chat
    await access_guard.deny_admin_attempt(
        bot=ctx.bot,
        owner_chat_id=next(iter(sorted(OWNER_IDS)), 0),
        bot_name="rugmunchbot",
        surface=f"cb:{surface}",
        user_id=user.id if user else None,
        username=user.username if user else None,
        chat_id=chat.id if chat else None,
        chat_type=chat.type if chat else None,
        audit=db.log_admin_action,
    )


async def _dispatch_admin_cb(update: Update, ctx, data: str) -> None:
    """Handle admin_* callbacks. Owner-only; every action is logged."""
    query = update.callback_query
    if query is None:
        return
    if not is_owner(query.from_user.id):
        await query.answer("\u26a0\ufe0f Owner only.", show_alert=True)
        await _deny_callback_attempt(update, ctx, query.data or "admin-cb")
        return

    from app.domains.telegram.rugmunchbot.handlers.owner import (
        cb_broadcast_confirm,
        pending_broadcast,
    )

    owner_uid = query.from_user.id
    msg = query.message
    if msg is None:
        await query.answer("Message unavailable.", show_alert=True)
        return
    chat_id = msg.chat.id

    if data == "admin_stats":
        await _admin_stats_card(query)
    elif data == "admin_logs":
        await _admin_logs_card(query)
    elif data == "admin_user_prompt":
        await query.edit_message_text(
            "\U0001f465 <b>User Lookup</b>\n\nUse <code>/admin_user &lt;user_id&gt;</code>\n\n"
            "Example: /admin_user 123456789",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data == "admin_broadcast_prompt":
        await query.edit_message_text(
            "\U0001f4e2 <b>Broadcast</b>\n\nUse <code>/admin_broadcast &lt;message&gt;</code> "
            "\u2014 you'll confirm before it sends.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data in ("admin_toggle_bot", "admin_toggle_bot_on", "admin_toggle_bot_off"):
        new_state = data.split("_")[-1] if data != "admin_toggle_bot" else None
        current = db.get_bot_state("bot_enabled", "on")
        if new_state in ("on", "off"):
            db.set_bot_state("bot_enabled", new_state)
            db.log_admin_action(owner_uid, "admin_toggle_bot", f"{current}->{new_state}")
            await query.edit_message_text(
                f"\U0001f6e1\ufe0f Bot is now <b>{'ENABLED' if new_state == 'on' else 'DISABLED (maintenance)'}</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_kb(),
            )
        else:
            await query.edit_message_text(
                f"\U0001f6e1\ufe0f Bot is currently <b>{current.upper()}</b>. Choose:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("\U0001f504 Enable", callback_data="admin_toggle_bot_on"),
                            InlineKeyboardButton("\U0001f6d1 Disable", callback_data="admin_toggle_bot_off"),
                        ],
                        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                    ]
                ),
            )
    elif data in ("admin_toggle_chat", "admin_toggle_chat_on", "admin_toggle_chat_off"):
        new_state = data.split("_")[-1] if data != "admin_toggle_chat" else None
        current = db.get_bot_state("chat_enabled", "on")
        if new_state in ("on", "off"):
            db.set_bot_state("chat_enabled", new_state)
            db.log_admin_action(owner_uid, "admin_toggle_chat", f"{current}->{new_state}")
            await query.edit_message_text(
                f"\U0001f4ac AI Chat is now <b>{'ENABLED' if new_state == 'on' else 'DISABLED'}</b> "
                "(commands keep working).",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_kb(),
            )
        else:
            await query.edit_message_text(
                f"\U0001f4ac AI Chat is currently <b>{current.upper()}</b>. Choose:",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("\U0001f504 Enable", callback_data="admin_toggle_chat_on"),
                            InlineKeyboardButton("\U0001f4f5 Disable", callback_data="admin_toggle_chat_off"),
                        ],
                        [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
                    ]
                ),
            )
    elif data == "admin_confirm_broadcast":
        if not pending_broadcast(owner_uid):
            await query.edit_message_text(
                "\u274c No pending broadcast.", parse_mode=ParseMode.HTML, reply_markup=admin_kb()
            )
            return
        await query.edit_message_text("\U0001f4e2 <b>Sending broadcast...</b>", parse_mode=ParseMode.HTML)
        await cb_broadcast_confirm(owner_uid, chat_id, ctx.bot)
    elif data == "admin_cancel":
        from app.domains.telegram.rugmunchbot.handlers.owner import _pending_broadcast

        _pending_broadcast.pop(owner_uid, None)
        await query.edit_message_text("\u274c Broadcast cancelled.", parse_mode=ParseMode.HTML, reply_markup=admin_kb())
    elif data.startswith("admin_user_ban_"):
        target = data[len("admin_user_ban_") :]
        try:
            target_id = int(target)
        except (ValueError, TypeError):
            await query.answer("Invalid user id.", show_alert=True)
            return
        ok = db.set_banned(target_id, True)
        db.log_admin_action(owner_uid, "admin_ban", str(target_id))
        await query.edit_message_text(
            f"\U0001f6ab User <code>{target_id}</code> {'banned.' if ok else 'not found.'}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    elif data.startswith("admin_user_tier_"):
        target = data[len("admin_user_tier_") :]
        await query.edit_message_text(
            f"\U0001f3af <b>Set Tier for {target}</b>\n\n"
            f"Use <code>/admin_set_tier {target} &lt;tier&gt; [days]</code>\n\n"
            f"Tiers: {', '.join(TIERS.keys())}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_kb(),
        )
    else:
        await query.answer("Unknown admin action.")


async def _admin_stats_card(query) -> None:
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    conn.close()
    active_today = db.count_active_today()
    ds = db.get_daily_stats()
    text = (
        f"\U0001f4ca <b>Bot Stats</b>\n{thin_sep()}\n"
        f"\U0001f465 Users: <b>{total}</b> | Active today: <b>{active_today}</b>\n"
        f"\U0001f6ab Banned: <b>{banned}</b>\n\n"
        f"\U0001f4ca <b>Today (UTC)</b>\n{thin_sep()}\n"
        f"\U0001f4ac Messages: <b>{ds['messages']}</b>\n"
        f"\U0001f4d6 Commands: <b>{ds['commands']}</b>\n"
        f"\U0001f50d RAG queries: <b>{ds['rag_queries']}</b>\n"
        f"\U0001f916 AI tokens: <b>{ds['ai_tokens']:,}</b>"
    )
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_kb())


async def _admin_logs_card(query) -> None:
    from datetime import UTC, datetime

    logs = db.get_admin_logs(limit=10)
    if not logs:
        await query.edit_message_text(
            "\U0001f4dd No admin actions logged yet.", parse_mode=ParseMode.HTML, reply_markup=admin_kb()
        )
        return
    lines = ["\U0001f4dd <b>Recent Admin Actions</b>", thin_sep()]
    for entry in logs:
        ts = datetime.fromtimestamp(entry.get("created_at", 0), tz=UTC).strftime("%m-%d %H:%M")
        details = entry.get("details") or ""
        lines.append(f"\u2022 <code>{ts}</code> <b>{entry.get('action')}</b> {details[:50]}")
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_kb())
