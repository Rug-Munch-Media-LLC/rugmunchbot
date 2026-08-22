"""Owner/admin dashboards — /admin, /admin_stats, /credits, /users.

Extracted verbatim from the former single-module ``handlers/owner.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.formatters import is_owner, thin_sep
from rugmunchbot.handlers.owner._shared import (
    _deny_admin_attempt,
    _fetch_credit_block,
    _format_model_usage_line,
    _log,
)
from rugmunchbot.keyboards import admin_kb

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin — admin dashboard with stats and actions."""
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
        f"{_format_model_usage_line()}\n\n"
        f"{await _fetch_credit_block()}\n\n"
        f"\U0001f6e1\ufe0f Bot: <b>{db.get_bot_state('bot_enabled', 'on')}</b> | "
        f"\U0001f4ac Chat: <b>{db.get_bot_state('chat_enabled', 'on')}</b>\n\n"
        f"<i>Use /admin_stats for full details. Actions are logged.</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=admin_kb())  # type: ignore[union-attr]  # PTB None-able fields


async def cmd_admin_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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


async def cmd_admin_credits(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/credits — live OpenRouter credit balance (owner-only)."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    _log(uid, "admin_credits")
    block = await _fetch_credit_block()
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"{block}\n\n<i>Checked live from OpenRouter \u2014 the API key is never shown.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=admin_kb(),
    )


async def cmd_admin_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/users — user count, top users, DAU (owner-only)."""
    uid = update.effective_user.id  # type: ignore[union-attr]  # PTB None-able fields
    if not is_owner(uid):
        await _deny_admin_attempt(update, ctx)
        return
    _log(uid, "admin_users")
    conn = db.get_db()
    total = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    banned = conn.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1").fetchone()["c"]
    top = conn.execute(
        "SELECT user_id, username, tier, total_scans, total_ai_msgs "
        "FROM users ORDER BY (total_scans + total_ai_msgs) DESC LIMIT 10"
    ).fetchall()
    conn.close()
    active_today = db.count_active_today()
    lines = [
        "\U0001f465 <b>Users</b>",
        thin_sep(),
        f"\U0001f4c8 Total: <b>{total}</b> | Active today (DAU): <b>{active_today}</b>",
        f"\U0001f6ab Banned: <b>{banned}</b>",
        thin_sep(),
        "\U0001f3c6 <b>Top 10 by activity</b>",
    ]
    for i, row in enumerate(top, 1):
        name = f"@{row['username']}" if row["username"] else f"<code>{row['user_id']}</code>"
        activity = int(row["total_scans"] or 0) + int(row["total_ai_msgs"] or 0)
        lines.append(f"{i}. {name} ({row['tier']}) \u2014 {activity:,} actions")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=admin_kb()
    )  # type: ignore[union-attr]  # PTB None-able fields
