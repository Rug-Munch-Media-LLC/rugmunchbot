"""Group call-tracking commands: /call, /calls, /lb, /fc."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler

from rugmunchbot.calltrack.model import parse_call
from rugmunchbot.calltrack.render import render_call_card, render_calls, render_leaderboard
from rugmunchbot.calltrack.store import (
    first_call,
    group_calls,
    init_calls_schema,
    leaderboard,
    record_call,
)

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

_WINDOW_SECONDS = {"1d": 86400, "7d": 604800, "1w": 604800, "30d": 2592000, "1mo": 2592000}


def _window(arg: str | None) -> tuple[int, str]:
    """(since_ts, label) for a `/lb 7d` style argument; default all-time."""
    key = (arg or "").lower()
    seconds = _WINDOW_SECONDS.get(key)
    if not seconds:
        return 0, "all time"
    return int(time.time()) - seconds, key


def _chat_id(update: Update) -> int:
    return int(update.effective_chat.id)


async def cmd_call(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/call <contract|$ticker> [note]` — log the token's first caller."""
    parsed = parse_call(list(ctx.args or []))
    if parsed is None:
        await update.message.reply_text(
            "Usage: <code>/call &lt;contract or $ticker&gt; [note]</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    user = update.effective_user
    stored, row = record_call(
        _chat_id(update),
        int(user.id),
        user.username,
        parsed["address"],
        chain=parsed["chain"],
        note=parsed["note"],
    )
    await update.message.reply_text(
        render_call_card(row, stored),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_calls(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/calls` — the group's most recent calls."""
    rows = group_calls(_chat_id(update))
    await update.message.reply_text(
        render_calls(rows), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_lb(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/lb [1d|7d|30d]` — the group's caller board (counts, no returns)."""
    args = list(ctx.args or [])
    since, label = _window(args[0] if args else None)
    rows = leaderboard(_chat_id(update), since_ts=since)
    await update.message.reply_text(render_leaderboard(rows, label), parse_mode=ParseMode.HTML)


async def cmd_fc(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/fc <contract>` — who called this token first in this chat."""
    args = list(ctx.args or [])
    parsed = parse_call(args)
    if parsed is None:
        await update.message.reply_text(
            "Usage: <code>/fc &lt;contract or $ticker&gt;</code>", parse_mode=ParseMode.HTML
        )
        return
    row = first_call(_chat_id(update), parsed["address"])
    if not row:
        await update.message.reply_text(
            "🕵️ No first caller logged for that token yet. <code>/call</code> it.",
            parse_mode=ParseMode.HTML,
        )
        return
    await update.message.reply_text(render_call_card(row, stored=False), parse_mode=ParseMode.HTML)


def register_calltrack(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Wire the surface: schema first, then the four commands."""
    init_calls_schema()
    for name, handler in (
        ("call", cmd_call),
        ("calls", cmd_calls),
        ("lb", cmd_lb),
        ("fc", cmd_fc),
    ):
        app.add_handler(CommandHandler(name, handler))
