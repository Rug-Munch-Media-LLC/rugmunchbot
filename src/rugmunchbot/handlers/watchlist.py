#!/usr/bin/env python3
"""
RugMunchBot -- Watchlist command handlers.

Commands: watchlist (backend-backed: list/add/remove/alert),
watch, unwatch, monitor, monitors, unmonitor
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

from telegram import LabeledPrice
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import SCAN_PACKS
from rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    sep,
)
from rugmunchbot.handlers.alerts import _unsubscribe_backend_monitor
from rugmunchbot.keyboards import back_kb, pricing_kb
from rugmunchbot.services.pricing import resolve
from rugmunchbot.support import backend_user
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import BackendApiError

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.watchlist")

# Monitor pack price comes from the same SCAN_PACKS table as the deep scan
# (250★ — mirrors the deep-scan invoice pattern, see handlers/payments.py).
MONITOR_STARS = int(SCAN_PACKS["monitor"]["stars"])
MONITOR_DAYS = db.MONITOR_DEFAULT_DAYS

_WL_USAGE = (
    "👀 <b>Watchlist</b>\n\n"
    "  <code>/watchlist</code> — show your watchlist\n"
    "  <code>/watchlist add &lt;symbol&gt; &lt;chain&gt; &lt;address&gt;</code>\n"
    "  <code>/watchlist remove &lt;id&gt;</code>\n"
    "  <code>/watchlist alert &lt;id&gt; &lt;above|below&gt; &lt;price&gt;</code>\n\n"
    "Example: <code>/watchlist add WETH ethereum 0xC02a…</code>"
)


def _short(address: str) -> str:
    return address[:8] + "…" if len(address) > 10 else address


def _alert_badge(entry: dict) -> str:
    above, below = entry.get("alert_above"), entry.get("alert_below")
    if above is not None:
        return f" 🔔↑{above}"
    if below is not None:
        return f" 🔔↓{below}"
    return ""


async def cmd_watchlist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Backend-backed watchlist: list / add / remove / alert subcommands."""
    if update.message is None:
        return
    args = ctx.args or []
    sub = args[0].lower() if args else ""
    if sub == "add":
        await _wl_add(update, args[1:])
    elif sub == "remove":
        await _wl_remove(update, args[1:])
    elif sub == "alert":
        await _wl_alert(update, args[1:])
    else:
        await _wl_show(update)


async def _wl_show(update: Update) -> None:
    """List the user's backend watchlist with live prices, 24h change, alerts."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    try:
        data = await backend_user.get_watchlist(
            user.id, username=user.username, first_name=user.first_name
        )
    except BackendApiError as exc:
        logger.warning("watchlist_backend_error user=%s: %s", user.id, exc)
        await update.message.reply_text(f"❌ Could not load your watchlist ({exc})")
        return
    entries = data.get("entries") or []
    if not entries:
        await update.message.reply_text(
            "👀 <b>Watchlist Empty</b>\n\n" + _WL_USAGE,
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    lines = [f"👀 <b>Your Watchlist</b> ({len(entries)} tokens)\n"]
    for entry in entries[:20]:
        symbol = entry.get("symbol") or "???"
        chain = entry.get("chain") or "?"
        address = entry.get("address") or ""
        eid = entry.get("id")
        price_txt = ""
        if address:
            pr = await resolve(address=address, chain=chain)
            if pr.price_usd:
                chg = f" · 24h {pr.change_24h:+.1f}%" if pr.change_24h is not None else ""
                price_txt = f" — ${pr.price_usd:,.6g}{chg}"
        lines.append(
            f"• <b>#{eid}</b> <b>{symbol}</b> ({chain}) "
            f"<code>{_short(address)}</code>{price_txt}{_alert_badge(entry)}"
        )
    lines.append(f"\n{footer_links()}")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def _wl_add(update: Update, args: list[str]) -> None:
    """/watchlist add <symbol> <chain> <address>."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: <code>/watchlist add &lt;symbol&gt; &lt;chain&gt; &lt;address&gt;</code>\n"
            "Example: <code>/watchlist add WETH ethereum 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    symbol, chain, address = args[0].upper(), args[1].lower(), args[2]
    try:
        data = await backend_user.add_watchlist(
            user.id,
            symbol,
            chain,
            address,
            username=user.username,
            first_name=user.first_name,
        )
    except BackendApiError as exc:
        logger.warning("watchlist_add_error user=%s: %s", user.id, exc)
        await update.message.reply_text(f"❌ Could not add token ({exc})")
        return
    entry = data.get("entry") or {}
    await update.message.reply_text(
        f"✅ Added <b>{symbol}</b> ({chain}) to your watchlist — id <b>#{entry.get('id')}</b>.\n"
        "/watchlist to view all.",
        parse_mode=ParseMode.HTML,
    )


async def _wl_remove(update: Update, args: list[str]) -> None:
    """/watchlist remove <id>."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    if not args:
        await update.message.reply_text(
            "Usage: <code>/watchlist remove &lt;id&gt;</code> — find IDs with /watchlist",
            parse_mode=ParseMode.HTML,
        )
        return
    entry_id = args[0]
    try:
        await backend_user.remove_watchlist(
            user.id, entry_id, username=user.username, first_name=user.first_name
        )
    except BackendApiError as exc:
        if exc.status_code == 404:
            await update.message.reply_text("❌ No watchlist entry with that id.")
        else:
            logger.warning("watchlist_remove_error user=%s: %s", user.id, exc)
            await update.message.reply_text(f"❌ Could not remove entry ({exc})")
        return
    await update.message.reply_text(
        f"✅ Removed entry <b>#{entry_id}</b>.", parse_mode=ParseMode.HTML
    )


async def _wl_alert(update: Update, args: list[str]) -> None:
    """/watchlist alert <id> <above|below> <price>."""
    user = update.effective_user
    if user is None or update.message is None:
        return
    if len(args) < 3 or args[1].lower() not in ("above", "below"):
        await update.message.reply_text(
            "Usage: <code>/watchlist alert &lt;id&gt; &lt;above|below&gt; &lt;price&gt;</code>\n"
            "Example: <code>/watchlist alert 3 above 3000</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    entry_id, direction, raw_price = args[0], args[1].lower(), args[2]
    try:
        price = float(raw_price)
        if price <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Price must be a positive number.")
        return
    try:
        await backend_user.set_watchlist_alert(
            user.id,
            entry_id,
            direction,
            price,
            username=user.username,
            first_name=user.first_name,
        )
    except BackendApiError as exc:
        if exc.status_code == 404:
            await update.message.reply_text("❌ No watchlist entry with that id.")
        else:
            logger.warning("watchlist_alert_error user=%s: %s", user.id, exc)
            await update.message.reply_text(f"❌ Could not set alert ({exc})")
        return
    arrow = "↑" if direction == "above" else "↓"
    await update.message.reply_text(
        f"🔔 Alert set: <b>#{entry_id}</b> {arrow} ${price:g}",
        parse_mode=ParseMode.HTML,
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
        await update.message.reply_text(
            "\u274c Token not found in your watchlist.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields


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
    from rugmunchbot.states import _pending_monitors

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
        from rugmunchbot.support.logging import get_logger

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
        text += f"\u2022 <b>{m.get('symbol') or '???'}</b> <code>{m['token_address']}</code> ({m.get('chain') or '?'})\n"
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
        await update.message.reply_text(
            "\u274c No active monitor for that token.", parse_mode=ParseMode.HTML
        )  # type: ignore[union-attr]  # PTB None-able fields
