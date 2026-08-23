"""Owner/admin user management — toggles, user lookup, tiers, bans, logs.

Extracted verbatim from the former single-module ``handlers/owner.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import OWNER_IDS, TIERS
from rugmunchbot.formatters import is_owner, thin_sep
from rugmunchbot.handlers.owner._shared import (
    _deny_admin_attempt,
    _log,
    _usage_line,
)
from rugmunchbot.keyboards import admin_kb

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_admin_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_toggle [on|off] — globally enable/disable the bot (maintenance)."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    current = db.get_bot_state("bot_enabled", "on")
    if ctx.args and ctx.args[0].lower() in ("on", "off"):
        new_state = ctx.args[0].lower()
        db.set_bot_state("bot_enabled", new_state)
        _log(uid, "admin_toggle_bot", f"{current}->{new_state}")
        icon = "\U0001f6e1\ufe0f" if new_state == "on" else "\U0001f534"
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"{icon} Bot is now <b>{'ENABLED' if new_state == 'on' else 'DISABLED (maintenance)'}</b>.\n\n"
            + (
                "All non-owner users will see a maintenance notice."
                if new_state == "off"
                else "The bot is accepting all users again."
            ),
            parse_mode=ParseMode.HTML,
        )
        return
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f6e1\ufe0f Bot is currently <b>{current.upper()}</b>.\n\nUse /admin_toggle on|off, or pick an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("\U0001f504 Enable", callback_data="admin_toggle_bot_on"),
                    InlineKeyboardButton(
                        "\U0001f6d1 Disable", callback_data="admin_toggle_bot_off"
                    ),
                ],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        ),
    )


async def cmd_admin_toggle_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_toggle_chat [on|off] — disable/enable AI chat (commands keep working)."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    current = db.get_bot_state("chat_enabled", "on")
    if ctx.args and ctx.args[0].lower() in ("on", "off"):
        new_state = ctx.args[0].lower()
        db.set_bot_state("chat_enabled", new_state)
        _log(uid, "admin_toggle_chat", f"{current}->{new_state}")
        icon = "\U0001f4ac" if new_state == "on" else "\U0001f4f5"
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"{icon} AI Chat is now <b>{'ENABLED' if new_state == 'on' else 'DISABLED'}</b>.\n\n"
            "Commands (/scan, /price, /trending ...) keep working either way.",
            parse_mode=ParseMode.HTML,
        )
        return
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f4ac AI Chat is currently <b>{current.upper()}</b>.\n\n"
        "Use /admin_toggle_chat on|off, or pick an action:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("\U0001f504 Enable", callback_data="admin_toggle_chat_on"),
                    InlineKeyboardButton(
                        "\U0001f4f5 Disable", callback_data="admin_toggle_chat_off"
                    ),
                ],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        ),
    )


async def cmd_admin_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_user <user_id> — show user details, tier, usage."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_user <user_id>")  # type: ignore[union-attr]  # PTB None-able fields
        return
    try:
        target = int(ctx.args[0])
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid user_id. Use the numeric Telegram ID.")  # type: ignore[union-attr]  # PTB None-able fields
        return
    user = db.get_user_by_id(target)
    if not user:
        await update.message.reply_text(
            f"\u274c User <code>{target}</code> not found.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields
        return
    _log(uid, "admin_user_lookup", str(target))
    created = (
        datetime.fromtimestamp(user.get("created_at", 0), tz=UTC).strftime("%Y-%m-%d")
        if user.get("created_at")
        else "?"
    )
    last = (
        datetime.fromtimestamp(user.get("last_active", 0), tz=UTC).strftime("%Y-%m-%d %H:%M")
        if user.get("last_active")
        else "?"
    )
    tier_exp = ""
    if user.get("tier_expires"):
        tier_exp = (
            f" (exp {datetime.fromtimestamp(user['tier_expires'], tz=UTC).strftime('%Y-%m-%d')})"
        )
    status_badge = "\U0001f6ab BANNED" if user.get("is_banned") else "\u2705 Active"
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f464 <b>User {target}</b>\n{thin_sep()}\n"
        f"Username: @{user.get('username') or '?'}\n"
        f"Name: {user.get('first_name') or '?'}\n"
        f"Tier: <b>{user.get('tier')}</b>{tier_exp}\n"
        f"Status: {status_badge}\n\n"
        f"\U0001f4ca <b>Usage</b>\n{thin_sep()}\n"
        f"{_usage_line(target)}\n"
        f"All-time scans: {user.get('total_scans', 0)} | AI msgs: {user.get('total_ai_msgs', 0)}\n"
        f"Bonus scans: {user.get('bonus_scans', 0)} | Bonus AI: {user.get('bonus_ai_msgs', 0)}\n"
        f"Stars: {user.get('stars_balance', 0)}\u2b50\n\n"
        f"Created: {created} | Last active: {last}",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "\U0001f3af Set Tier", callback_data=f"admin_user_tier_{target}"
                    ),
                    InlineKeyboardButton(
                        "\U0001f6ab Ban", callback_data=f"admin_user_ban_{target}"
                    ),
                ],
                [InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")],
            ]
        ),
    )


async def cmd_admin_set_tier(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_set_tier <user_id> <tier> [days] — change user tier."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    ctx.args = ctx.args or []
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /admin_set_tier <user_id> <tier> [days]")  # type: ignore[union-attr]  # PTB None-able fields
        return
    try:
        target, tier = int(ctx.args[0]), ctx.args[1].lower()
    except (ValueError, TypeError):
        await update.message.reply_text("Usage: /admin_set_tier <user_id> <tier> [days]")  # type: ignore[union-attr]  # PTB None-able fields
        return
    try:
        days = int(ctx.args[2]) if len(ctx.args) > 2 else 30
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid days value. Use a whole number of days.")  # type: ignore[union-attr]  # PTB None-able fields
        return
    if not 1 <= days <= 3650:
        await update.message.reply_text("Days must be between 1 and 3650.")  # type: ignore[union-attr]  # PTB None-able fields
        return
    if tier not in TIERS:
        await update.message.reply_text(f"Invalid tier. Options: {', '.join(TIERS.keys())}")  # type: ignore[union-attr]  # PTB None-able fields
        return
    if not db.get_user_by_id(target):
        await update.message.reply_text(
            f"\u274c User <code>{target}</code> not found.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields
        return
    db.set_user_tier(target, tier, days)
    _log(uid, "admin_set_tier", f"user={target} tier={tier} days={days}")
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\u2705 Set user {target} to <b>{tier}</b> ({days} days)", parse_mode=ParseMode.HTML
    )


async def cmd_admin_ban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_ban <user_id> — ban user."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_ban <user_id>")  # type: ignore[union-attr]  # PTB None-able fields
        return
    try:
        target = int(ctx.args[0])
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid user_id. Use the numeric Telegram ID.")  # type: ignore[union-attr]  # PTB None-able fields
        return
    if target in OWNER_IDS:
        await update.message.reply_text("\u274c Cannot ban the owner.")  # type: ignore[union-attr]  # PTB None-able fields
        return
    ok = db.set_banned(target, True)
    _log(uid, "admin_ban", str(target))
    if ok:
        await update.message.reply_text(
            f"\U0001f6ab Banned user {target}.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields
    else:
        await update.message.reply_text(
            f"\u274c User {target} not found.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields


async def cmd_admin_unban(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_unban <user_id> — unban user."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_unban <user_id>")  # type: ignore[union-attr]  # PTB None-able fields
        return
    try:
        target = int(ctx.args[0])
    except (ValueError, TypeError):
        await update.message.reply_text("Invalid user_id. Use the numeric Telegram ID.")  # type: ignore[union-attr]  # PTB None-able fields
        return
    ok = db.set_banned(target, False)
    _log(uid, "admin_unban", str(target))
    if ok:
        await update.message.reply_text(
            f"\u2705 Unbanned user {target}.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields
    else:
        await update.message.reply_text(
            f"\u274c User {target} not found.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields


async def cmd_admin_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_logs — recent admin actions."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    logs = db.get_admin_logs(limit=15)
    if not logs:
        await update.message.reply_text(
            "\U0001f4dd No admin actions logged yet.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields
        return
    lines = ["\U0001f4dd <b>Recent Admin Actions</b>", thin_sep()]
    for entry in logs:
        ts = datetime.fromtimestamp(entry.get("created_at", 0), tz=UTC).strftime("%m-%d %H:%M")
        details = entry.get("details") or ""
        lines.append(f"\u2022 <code>{ts}</code> <b>{entry.get('action')}</b> {details[:60]}")
    lines.append(thin_sep())
    lines.append(f"<i>Total actions stored: {len(db.get_admin_logs(limit=10000))}</i>")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_kb()
    )  # type: ignore[union-attr]  # PTB None-able fields
