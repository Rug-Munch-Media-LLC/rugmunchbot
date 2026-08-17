#!/usr/bin/env python3
"""
RugMunchBot -- Callback query handler.

Handles all inline keyboard button presses.

The historic single-function ``handle_callback`` (1000 lines) was split by
callback-data prefix into per-group handler modules (mirroring the adminbot
split): ``_menus``, ``_actions``, ``_refresh`` and ``_invoices``. The public
entry point is unchanged:

    from app.domains.telegram.rugmunchbot.handlers.callback import handle_callback
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.formatters import footer_links
from app.domains.telegram.rugmunchbot.handlers.callback import _actions, _invoices, _menus, _refresh
from app.domains.telegram.rugmunchbot.handlers.callback._admin import _dispatch_admin_cb
from app.domains.telegram.rugmunchbot.handlers.callback._inline import (
    _inline_market_data,
    _inline_news,
    _inline_sentiment,
    _inline_trending,
)
from app.domains.telegram.rugmunchbot.handlers.callback._run_cards import _RUN_CARDS
from app.domains.telegram.rugmunchbot.keyboards import back_kb

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Route inline keyboard callbacks (menus, scans, payments, admin) by data prefix."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data
    if data is None:
        return
    user = query.from_user
    db.get_or_create_user(user.id, user.username, user.first_name)

    # Ordered dispatchers — MUST preserve the original if/elif chain order.
    #
    # Original branch positions (single-function chain):
    #   1-35   menu_*/open_* exact matches   -> _menus
    #   18     run_*                         -> _dispatch_run_cmd
    #   19     admin_*                       -> _dispatch_admin_cb
    #   32-55  dismiss/scam/scan/share/...   -> _actions
    #   30-64  refresh/reload branches       -> _refresh
    #   50,65-68 invoices (buystars/deep/...) -> _invoices
    #
    # Constraints enforced by this order:
    #   - _actions dispatches BEFORE _refresh so the generic ``sentiment_``
    #     branch keeps shadowing the exact ``sentiment_refresh`` branch, as
    #     in the original chain.
    #   - _menus dispatches first: all its branches are exact matches with
    #     no prefix overlap against the later modules.
    if await _menus.handle(update, ctx, query, user, data):
        return
    if data.startswith("run_"):
        await _dispatch_run_cmd(update, ctx, data[4:])
        return
    if data.startswith("admin_"):
        await _dispatch_admin_cb(update, ctx, data)
        return
    if await _actions.handle(update, ctx, query, user, data):
        return
    if await _refresh.handle(update, ctx, query, data):
        return
    if await _invoices.handle(update, ctx, query, user, data):
        return


# ══════════════════════════════════════════════
# v5 — run_* command cards dispatch
# ══════════════════════════════════════════════


async def _dispatch_run_cmd(update: Update, ctx, cmd_arg: str | None = None) -> None:
    """Handle run_* menu buttons: live inline data for trending/news/sentiment/
    market, real command execution for scan/price/chart, usage cards otherwise.

    ``cmd_arg`` is the raw callback payload after the ``run_`` prefix (from
    the callback handler). It is optional — when omitted (defensive) the
    command is re-derived from ``query.data``.
    """
    query = update.callback_query
    if query is None:
        return
    cmd = (query.data or "")[4:]

    if cmd == "trending":
        await _inline_trending(update, ctx)
        return
    if cmd == "news":
        await _inline_news(update, ctx)
        return
    if cmd == "sentiment":
        await _inline_sentiment(update, ctx)
        return
    if cmd in ("market", "movers", "launches", "whales"):
        await _inline_market_data(update, ctx, cmd)
        return
    if cmd in ("scan", "price", "chart"):
        await _exec_live_command(update, ctx, cmd)
        return

    card = _RUN_CARDS.get(cmd)
    if card is None:
        card = f"<code>/{cmd}</code> \u2014 check /help for usage."
    await query.edit_message_text(
        card + footer_links(),
        parse_mode=ParseMode.HTML,
        reply_markup=back_kb(),
        disable_web_page_preview=True,
    )


async def _exec_live_command(update: Update, ctx, cmd: str) -> None:
    """Execute a real command handler for arg-less run_* menu buttons.

    run_scan / run_price / run_chart carry no address payload, so we invoke
    the registered command with empty args — it renders its own usage/entry
    flow instead of a static card.
    """
    if cmd == "scan":
        from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_scan

        handler: Any = cmd_scan
    elif cmd == "price":
        from app.domains.telegram.rugmunchbot.handlers.price import cmd_price

        handler = cmd_price
    else:
        from app.domains.telegram.rugmunchbot.commands.chart import cmd_chart

        handler = cmd_chart
    ctx.args = []
    await handler(update, ctx)
