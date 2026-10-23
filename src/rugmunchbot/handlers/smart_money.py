"""Smart-money / whale surface: /smartmoney, /whales, /predictions.

Data comes only from live backend feeds; when a feed is down or empty the
reply names what was tried instead of showing stale or invented numbers.
Channel posting lives in ``rugmunchbot.smart_money.poster`` (dry-run default).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler

from rugmunchbot.smart_money._fmt import esc
from rugmunchbot.smart_money.hl import fetch_hl
from rugmunchbot.smart_money.moves import fetch_moves
from rugmunchbot.smart_money.predictions import fetch_predictions
from rugmunchbot.smart_money.render_hl import render_hl
from rugmunchbot.smart_money.render_move import render_move
from rugmunchbot.smart_money.render_prediction import render_prediction

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

_MOVES_LIMIT = 5
_HL_LIMIT = 4
_PRED_LIMIT = 4


def _fallback(title: str, feed: Any) -> str:
    tried = ", ".join(feed.tried) or "none"
    return (
        f"⚠️ <b>{esc(title)} unavailable</b>\n\n"
        f"{esc(feed.note or 'no rows')}\n"
        f"Tried: {esc(tried)}\n\n"
        "<i>No numbers shown rather than stale ones. Data feeds recover on "
        "the next tick — try again shortly.</i>"
    )


async def cmd_smartmoney(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/smartmoney` — live moves feed; `/smartmoney <address>` — token view."""
    args = ctx.args or []
    if args:
        from rugmunchbot.commands.smartmoney import cmd_smartmoney as token_view

        await token_view(update, ctx)
        return
    msg = await update.message.reply_text(
        "🧠 <b>Reading smart-money moves…</b>", parse_mode=ParseMode.HTML
    )
    feed = await fetch_moves(limit=_MOVES_LIMIT)
    if not feed.ok:
        await msg.edit_text(_fallback("Smart-money moves", feed), parse_mode=ParseMode.HTML)
        return
    post = render_move(feed, feed.items[0])
    extra = "".join(f"\n\n{render_move(feed, m)}" for m in feed.items[1:_MOVES_LIMIT])
    await msg.edit_text(str(post) + extra, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def cmd_whales(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/whales` — Hyperliquid movers, funding squeezes, liquidation clusters."""
    msg = await update.message.reply_text(
        "🐋 <b>Reading Hyperliquid whale flow…</b>", parse_mode=ParseMode.HTML
    )
    feed = await fetch_hl(limit=_HL_LIMIT)
    if not feed.ok:
        await msg.edit_text(_fallback("Hyperliquid snapshot", feed), parse_mode=ParseMode.HTML)
        return
    posts = [str(render_hl(feed, item)) for item in feed.items[:_HL_LIMIT]]
    await msg.edit_text(
        "\n\n".join(posts), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


async def cmd_predictions(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/predictions [n]` — prediction-market odds (aggregate books only)."""
    limit = _PRED_LIMIT
    if ctx.args:
        try:
            limit = max(1, min(6, int(ctx.args[0])))
        except (TypeError, ValueError):
            limit = _PRED_LIMIT
    msg = await update.message.reply_text(
        "🔮 <b>Reading prediction-market odds…</b>", parse_mode=ParseMode.HTML
    )
    feed = await fetch_predictions(limit=limit)
    if not feed.ok:
        await msg.edit_text(_fallback("Prediction markets", feed), parse_mode=ParseMode.HTML)
        return
    posts = [str(render_prediction(feed, item)) for item in feed.items]
    await msg.edit_text(
        "\n\n".join(posts), parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )


def register_smart_money(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the smart-money surface (menu stays under /more)."""
    app.add_handler(CommandHandler("smartmoney", cmd_smartmoney))
    app.add_handler(CommandHandler("whales", cmd_whales))
    app.add_handler(CommandHandler("predictions", cmd_predictions))
