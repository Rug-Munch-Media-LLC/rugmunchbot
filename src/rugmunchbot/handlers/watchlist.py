#!/usr/bin/env python3
"""
RugMunchBot -- Watchlist command handlers.

Commands: watchlist, watch, unwatch
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import TIERS
from rugmunchbot.formatters import (
    detect_chain,
    fmt_number,
    footer_links,
    sep,
)
from rugmunchbot.keyboards import back_kb, pricing_kb, token_buttons

if TYPE_CHECKING:
    from telegram import Update


def _tier_cfg(tier: str) -> dict:
    # [REVIEW] OSS config.py ships a single "free" tier (premium tiers are
    # feature flags). Guard the lookup the same way db.py does so a stored
    # premium tier never KeyErrors on the display line.
    return TIERS.get(tier, TIERS["free"])


async def cmd_watchlist(update: Update, ctx) -> None:
    user = update.effective_user
    if user is None:
        return
    items = db.get_watchlist(user.id)
    if not items:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f440 <b>Watchlist Empty</b>\n\n"
            "Add tokens to track:\n"
            "  /watch <code>address</code> [chain]\n\n"
            "<i>Scout: 10 slots | Hunter: 50 | Pro: unlimited</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    tier = db.get_user_tier(user.id)
    cfg = _tier_cfg(tier)
    text = f"\U0001f440 <b>Watchlist</b> ({cfg['emoji']} {cfg['name']})\n{sep()}\n\n"
    for i, w in enumerate(items, 1):
        alert = ""
        if w.get("alert_price"):
            arrow = "\u2191" if w.get("alert_direction") == "above" else "\u2193"
            alert = f" \U0001f514{arrow}{fmt_number(w['alert_price'])}"
        text += f"{i}. <b>{w.get('symbol') or '???'}</b> <code>{w['token_address']}</code> ({w['chain']}){alert}\n"
    text += f"\n{footer_links()}"
    rows: list[list[InlineKeyboardButton]] = []
    for w in items:
        rows.append(token_buttons(w["token_address"], w.get("chain") or "solana"))
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )


async def cmd_watch(update: Update, ctx) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f440 Usage: /watch <code>address</code> [chain]", parse_mode=ParseMode.HTML
        )
        return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else detect_chain(addr)
    symbol = ctx.args[2] if len(ctx.args) > 2 else None
    ok = db.add_watchlist(user.id, addr, chain, symbol)
    if ok:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2705 Added <code>{addr}</code> to watchlist.\n/watchlist to view all.",
            parse_mode=ParseMode.HTML,
        )
    else:
        tier = db.get_user_tier(user.id)
        if tier == "free":
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                "\u274c Watchlists require <b>Scout tier</b> or higher.\n\n"
                # [REVIEW] USD tier prices scrubbed (private billing — checklist §6 #17).
                "\U0001f50d <b>Scout tier</b> - 10 watchlist slots\n"
                "\U0001f3af <b>Hunter tier</b> - 50 slots + alerts\n"
                "\U0001f451 <b>Pro tier</b> - unlimited",
                parse_mode=ParseMode.HTML,
                reply_markup=pricing_kb(),
            )
        else:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                "\u274c Could not add. Limit reached or already added.", parse_mode=ParseMode.HTML
            )


async def cmd_unwatch(update: Update, ctx) -> None:
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "Usage: /unwatch <code>address</code>", parse_mode=ParseMode.HTML
        )
        return
    addr = ctx.args[0]
    ok = db.remove_watchlist(user.id, addr)
    if ok:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2705 Removed <code>{addr}</code> from watchlist.", parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u274c Token not found in your watchlist.", parse_mode=ParseMode.HTML
        )
