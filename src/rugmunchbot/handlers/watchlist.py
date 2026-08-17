#!/usr/bin/env python3
"""
RugMunchBot -- Watchlist command handlers.

Commands: watchlist, watch, unwatch, monitor, monitors, unmonitor
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import SCAN_PACKS, TIERS
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    fmt_number,
    footer_links,
    sep,
)
from app.domains.telegram.rugmunchbot.handlers.alerts import _unsubscribe_backend_monitor
from app.domains.telegram.rugmunchbot.keyboards import back_kb, pricing_kb, token_buttons

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

# Monitor pack price comes from the same SCAN_PACKS table as the deep scan
# (250★ — mirrors the deep-scan invoice pattern, see handlers/payments.py).
MONITOR_STARS = int(SCAN_PACKS["monitor"]["stars"])
MONITOR_DAYS = db.MONITOR_DEFAULT_DAYS


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List the user's watchlist with per-token alert/monitor badges."""
    user = update.effective_user
    if user is None:
        return
    items = db.get_watchlist(user.id)
    if not items:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f440 <b>Watchlist Empty</b>\n\n"
            "Add tokens to track:\n"
            "  /watch <code>address</code> [chain]\n\n"
            "<i>Free: 0 slots | Scout: 10 | Hunter: 50 | Pro: unlimited</i>\n\n"
            "\U0001f6f0\ufe0f <b>Monitor</b> (250\u2605/30d) adds live LP/whale + "
            "tax/ownership/mint change alerts: /monitor <code>address</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    tier = db.get_user_tier(user.id)
    text = f"\U0001f440 <b>Watchlist</b> ({TIERS[tier]['emoji']} {TIERS[tier]['name']})\n{sep()}\n\n"
    monitors = {m["token_address"].lower() for m in db.get_monitors(user.id)}
    for i, w in enumerate(items, 1):
        alert = ""
        if w.get("alert_price"):
            arrow = "\u2191" if w.get("alert_direction") == "above" else "\u2193"
            alert = f" \U0001f514{arrow}{fmt_number(w['alert_price'])}"
        mon = " \U0001f6f0\ufe0f" if w["token_address"].lower() in monitors else ""
        text += f"{i}. <b>{w.get('symbol') or '???'}</b> <code>{w['token_address']}</code> ({w['chain']}){alert}{mon}\n"
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


async def cmd_watch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a token to the watchlist (or show usage when unset / tier-blocked)."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f440 Usage: /watch <code>address</code> [chain]\n\n"
            "Example: /watch <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana",
            parse_mode=ParseMode.HTML,
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
                "\U0001f6f0\ufe0f <b>Monitor (250\u2605/30d)</b> \u2014 live LP/whale + "
                "tax/ownership/mint change alerts on any token\n"
                "\U0001f52c <b>Deep Scan (250\u2605)</b> \u2014 full forensics on demand\n"
                "\U0001f4b0 <b>Core /scan + chat stay free.</b>\n\n"
                "Run /monitor <code>address</code> to start a monitor.",
                parse_mode=ParseMode.HTML,
                reply_markup=pricing_kb(),
            )
        else:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                "\u274c Could not add. Limit reached or already added.", parse_mode=ParseMode.HTML
            )


async def cmd_unwatch(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a token from the watchlist."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "Usage: /unwatch <code>address</code>\n\n"
            "Example: /unwatch <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    addr = ctx.args[0]
    ok = db.remove_watchlist(user.id, addr)
    if ok:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2705 Removed <code>{addr}</code> from watchlist.", parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("\u274c Token not found in your watchlist.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields


# ── Monitor (Stars-paid premium feature) ─────────────────────────────────


async def cmd_monitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Start a paid monitor: /monitor <address> [chain].

    Sends a Telegram Stars invoice (250★ / 30 days, same pattern as the deep
    scan). On successful payment (handlers/payments.py ``mon_`` branch) the
    monitor is registered: a live webhook subscription (LP / whale / price
    events through the existing alert bridge + Helius polling) plus a
    security baseline snapshot that the ``check_monitors`` job re-scans for
    tax / ownership / mint changes — surfaced as DMs.
    """
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f6f0\ufe0f <b>Token Monitor</b>\n\n"
            "Watch a token for rug-relevant changes:\n"
            "\u2022 LP add/remove & lock changes\n"
            "\u2022 Whale + deployer wallet moves\n"
            "\u2022 Tax / ownership / mint changes\n"
            "\u2022 Price swings\n\n"
            f"Cost: <b>{MONITOR_STARS}\u2605</b> for {MONITOR_DAYS} days (Telegram Stars)\n\n"
            "Usage: /monitor <code>address</code> [chain]\n\n"
            "Example: /monitor <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "Check active: /monitors \u00b7 Stop: /unmonitor <code>address</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    addr = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else detect_chain(addr)
    if chain == "unknown":
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u274c Could not detect chain. Ensure it's a valid EVM (0x...) or Solana address.",
            parse_mode=ParseMode.HTML,
        )
        return

    existing = db.get_monitors(user.id)
    if any(m["token_address"].lower() == addr.lower() for m in existing):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2705 <code>{addr}</code> is already monitored.\nUse /monitors to see it.",
            parse_mode=ParseMode.HTML,
        )
        return

    token = secrets.token_hex(8)
    from app.domains.telegram.rugmunchbot.states import _pending_monitors

    _pending_monitors[token] = {
        "user_id": user.id,
        "address": addr,
        "chain": chain,
    }
    try:
        msg = update.message
        if msg is None:
            return
        await ctx.bot.send_invoice(
            chat_id=msg.chat.id,
            title="\U0001f6f0\ufe0f Token Monitor",
            description=f"30 days of live monitoring for {addr} on {chain}.",
            payload=f"mon_{token}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice("Token Monitor (30d)", MONITOR_STARS)],
        )
    except Exception as e:  # invoice failures must not crash the command
        from app.domains.telegram.rugmunchbot.support.logging import get_logger

        get_logger("rmi_bot").warning("monitor_invoice_error user=%s err=%s", user.id, e)
        _pending_monitors.pop(token, None)
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u274c Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_monitors(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """List active monitors for the user."""
    user = update.effective_user
    if user is None:
        return
    items = db.get_monitors(user.id)
    if not items:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f6f0\ufe0f <b>No Active Monitors</b>\n\n"
            "Watch tokens for LP/whale + tax/ownership/mint changes:\n"
            f"  /monitor <code>address</code> \u2014 {MONITOR_STARS}\u2605 / {MONITOR_DAYS} days",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    text = f"\U0001f6f0\ufe0f <b>Active Monitors</b>\n{sep()}\n\n"
    for m in items:
        text += (
            f"\u2022 <b>{m.get('symbol') or '???'}</b> <code>{m['token_address']}</code> ({m.get('chain') or '?'})\n"
        )
    text += f"\nStop: /unmonitor <code>address</code>\n{footer_links()}"
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        text, parse_mode=ParseMode.HTML, reply_markup=back_kb(), disable_web_page_preview=True
    )


async def cmd_unmonitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Stop a monitor: /unmonitor <address>."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "Usage: /unmonitor <code>address</code>\n\n"
            "Example: /unmonitor <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    addr = ctx.args[0]
    ok = db.remove_monitor(user.id, addr)
    if ok:
        await _unsubscribe_backend_monitor(user.id, addr)
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            f"\u2705 Stopped monitoring <code>{addr}</code>.", parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text("\u274c No active monitor for that token.", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
