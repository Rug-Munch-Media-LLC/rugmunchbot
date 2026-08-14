#!/usr/bin/env python3
"""
RugMunchBot -- Owner/admin command handlers (v5 admin panel).

Owner-only commands:
  /admin                  -> admin dashboard with bot stats + actions
  /admin_stats            -> users, active today, messages, RAG, AI tokens
  /admin_broadcast <msg>  -> broadcast to all users (with confirmation)
  /admin_toggle [on|off]  -> disable/enable the bot globally
  /admin_toggle_chat [on|off] -> disable/enable AI chat (commands keep working)
  /admin_user <user_id>   -> user details, tier, usage
  /admin_set_tier <uid> <tier> [days] -> change user tier
  /admin_ban <user_id>    -> ban user
  /admin_unban <user_id>  -> unban user
  /admin_logs             -> recent admin actions

Every action is written to the admin audit log (db.log_admin_action).
Owner gating is config-driven: ``BOT_OWNER_IDS`` env (comma ints), empty
default = admin commands disabled.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from rugmunchbot import db
from rugmunchbot.config import OWNER_IDS, TIERS
from rugmunchbot.formatters import is_owner, thin_sep
from rugmunchbot.keyboards import admin_confirm_kb, admin_kb
from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")

# Pending broadcast confirmation state (keyed by owner user_id).
_pending_broadcast: dict[int, str] = {}


def _log(admin_id: int, action: str, details: str = "") -> None:
    db.log_admin_action(admin_id, action, details)


async def _deny_admin_attempt(update: Update, ctx) -> None:
    """Log + audit a non-owner admin attempt (defense-in-depth). Fail-open.

    The live bot also alerts the owner through a private owner-alert rate
    limiter (kept in the private monorepo). OSS drops the alert; the attempt
    is still recorded in the admin audit log.
    """
    user = update.effective_user
    chat = update.effective_chat
    surface = "admin-cmd"
    if update.callback_query is not None and update.callback_query.data:
        surface = f"cb:{update.callback_query.data}"
    elif update.message is not None and update.message.text:
        surface = f"cmd:{update.message.text.split()[0]}"
    db.log_admin_action(
        0,
        "denied_admin_attempt",
        f"surface={surface} user={user.id if user else None} "
        f"username={user.username if user else None} "
        f"chat={chat.id if chat else None} type={chat.type if chat else None}",
    )
    logger.warning(
        "denied_admin_attempt",
        surface=surface,
        user_id=user.id if user else None,
    )


def _usage_line(uid: int) -> str:
    scans, ai = db.get_weekly_usage(uid)
    return f"Scans: {scans} | AI msgs: {ai}"


async def cmd_admin(update: Update, ctx) -> None:
    """/admin — admin dashboard with stats and quick actions."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    _log(uid, "admin_dashboard")
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    conn.close()
    active_today = db.count_active_today()
    ds = db.get_daily_stats()
    text = (
        f"\U0001f6e1\ufe0f <b>Admin Dashboard</b>\n{thin_sep()}\n"
        f"\U0001f465 Users: <b>{total}</b> | Active today: <b>{active_today}</b>\n"
        f"\U0001f6ab Banned: <b>{banned}</b>\n\n"
        f"\U0001f4ca <b>Today</b>\n{thin_sep()}\n"
        f"\U0001f4ac Messages: {ds['messages']} | \U0001f4d6 Commands: {ds['commands']}\n"
        f"\U0001f50d RAG queries: {ds['rag_queries']} | \U0001f916 AI tokens: {ds['ai_tokens']:,}\n\n"
        f"\U0001f6e1\ufe0f Bot: <b>{db.get_bot_state('bot_enabled', 'on')}</b> | "
        f"\U0001f4ac Chat: <b>{db.get_bot_state('chat_enabled', 'on')}</b>\n\n"
        f"<i>Use /admin_stats for full details. Actions are logged.</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_kb())  # type: ignore[union-attr]  # PTB None-able fields


async def cmd_admin_stats(update: Update, ctx) -> None:
    """/admin_stats — users count, active today, messages today, RAG queries, AI tokens."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    _log(uid, "admin_stats")
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    tiers = conn.execute("SELECT tier, COUNT(*) as c FROM users GROUP BY tier").fetchall()
    scans_today = (
        conn.execute(
            "SELECT SUM(scans) as c FROM weekly_usage WHERE week_start = ?",
            (db._current_week_start(),),
        ).fetchone()["c"]
        or 0
    )
    total_scans = conn.execute("SELECT SUM(total_scans) as c FROM users").fetchone()["c"] or 0
    total_ai = conn.execute("SELECT SUM(total_ai_msgs) as c FROM users").fetchone()["c"] or 0
    banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    conn.close()
    active_today = db.count_active_today()
    ds = db.get_daily_stats()
    tier_str = "\n".join(f"  {t['tier']}: {t['c']}" for t in tiers)
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f4ca <b>Bot Stats</b>\n{thin_sep()}\n"
        f"\U0001f465 Total Users: <b>{total}</b>\n"
        f"\U0001f4c8 Active Today: <b>{active_today}</b>\n"
        f"\U0001f6ab Banned: <b>{banned}</b>\n\n"
        f"\U0001f4c8 <b>Tiers:</b>\n{tier_str}\n\n"
        f"\U0001f4ca <b>Today (UTC)</b>\n{thin_sep()}\n"
        f"\U0001f4ac Messages: <b>{ds['messages']}</b>\n"
        f"\U0001f4d6 Commands: <b>{ds['commands']}</b>\n"
        f"\U0001f50d RAG queries: <b>{ds['rag_queries']}</b>\n"
        f"\U0001f916 AI tokens used: <b>{ds['ai_tokens']:,}</b>\n\n"
        f"\U0001f50d This Week Scans: <b>{scans_today}</b>\n"
        f"\U0001f50d All-Time Scans: <b>{total_scans}</b>\n"
        f"\U0001f916 All-Time AI msgs: <b>{total_ai}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_kb(),
    )


async def cmd_admin_broadcast(update: Update, ctx) -> None:
    """/admin_broadcast <message> — request confirmation, then broadcast."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    if not ctx.args:
        await update.message.reply_text("Usage: /admin_broadcast <message>")  # type: ignore[union-attr]  # PTB None-able fields
        return
    msg_text = " ".join(ctx.args)
    conn = db.get_db()
    count = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 0").fetchone()["c"]
    conn.close()
    _pending_broadcast[uid] = msg_text
    _log(uid, "admin_broadcast_pending", f"recipients={count} preview={msg_text[:80]}")
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f4e2 <b>Broadcast Preview</b>\n{thin_sep()}\n\n{msg_text}\n\nSend this to <b>{count}</b> active users?",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_confirm_kb("broadcast"),
    )


async def _do_broadcast(bot: Bot, msg_text: str, owner_uid: int | None = None) -> tuple[int, int]:
    conn = db.get_db()
    users = conn.execute("SELECT user_id FROM users WHERE is_banned = 0").fetchall()
    conn.close()
    sent = failed = 0
    for u in users:
        try:
            await bot.send_message(u["user_id"], msg_text, parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            # Flood control: back off as Telegram instructs, retry once, then skip.
            retry_after = e.retry_after
            retry_secs = (
                retry_after.total_seconds()
                if isinstance(retry_after, timedelta)
                else float(retry_after)
            )
            await asyncio.sleep(retry_secs + 1)
            try:
                await bot.send_message(u["user_id"], msg_text, parse_mode=ParseMode.HTML)
                sent += 1
            except TelegramError as retry_err:
                logger.debug(f"Broadcast retry failed for user {u['user_id']}: {retry_err}")
                failed += 1
        except TelegramError as e:
            # Blocked (Forbidden), malformed (BadRequest), or transient API error:
            # skip this recipient and keep fanning out.
            logger.debug(f"Broadcast skipped for user {u['user_id']}: {e}")
            failed += 1
        except (KeyError, TypeError) as e:
            # Programming error: surface it, abort the fan-out.
            logger.debug(f"Broadcast aborted for user {u['user_id']}: {e}")
            raise
    if owner_uid is not None:
        _log(owner_uid, "admin_broadcast", f"sent={sent} failed={failed}")
    return sent, failed


async def cmd_admin_toggle(update: Update, ctx) -> None:
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


async def cmd_admin_toggle_chat(update: Update, ctx) -> None:
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


async def cmd_admin_user(update: Update, ctx) -> None:
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


async def cmd_admin_set_tier(update: Update, ctx) -> None:
    """/admin_set_tier <user_id> <tier> [days] — change user tier."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Usage: /admin_set_tier <user_id> <tier> [days]")  # type: ignore[union-attr]  # PTB None-able fields
        return
    try:
        target, tier = int(ctx.args[0]), ctx.args[1].lower()
    except (ValueError, TypeError):
        await update.message.reply_text("Usage: /admin_set_tier <user_id> <tier> [days]")  # type: ignore[union-attr]  # PTB None-able fields
        return
    days = int(ctx.args[2]) if len(ctx.args) > 2 else 30
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


async def cmd_admin_ban(update: Update, ctx) -> None:
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
        await update.message.reply_text("Invalid user_id.")  # type: ignore[union-attr]  # PTB None-able fields
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


async def cmd_admin_unban(update: Update, ctx) -> None:
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
        await update.message.reply_text("Invalid user_id.")  # type: ignore[union-attr]  # PTB None-able fields
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


async def cmd_admin_logs(update: Update, ctx) -> None:
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


# ── Callback helpers (used by handlers/callback.py) ──


async def cb_broadcast_confirm(
    owner_uid: int, chat_id: int, bot, message_id: int | None = None
) -> None:
    """Execute a confirmed broadcast."""
    msg_text = _pending_broadcast.pop(owner_uid, "")
    if not msg_text:
        return
    sent, failed = await _do_broadcast(bot, msg_text, owner_uid=owner_uid)
    try:
        await bot.send_message(
            chat_id,
            f"\U0001f4e2 Broadcast complete.\n\u2705 Sent: {sent}\n\u274c Failed: {failed}",
            parse_mode=ParseMode.HTML,
        )
    except (Forbidden, BadRequest, KeyError, TypeError):
        logger.debug("broadcast summary send failed chat=%s", chat_id)


def pending_broadcast(owner_uid: int) -> str | None:
    return _pending_broadcast.get(owner_uid)
