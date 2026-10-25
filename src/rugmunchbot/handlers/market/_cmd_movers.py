"""market /movers — top gainers / losers for a timeframe."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot.handlers._market_helpers import _window_change
from rugmunchbot.keyboards import token_buttons

from ._constants import MARKET_UNAVAILABLE
from ._data import _fetch_market_intel
from ._render import _MOVERS_TF_LABEL, _render_movers_lines

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def cmd_movers(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /movers [1h|6h|24h|7d] — top gainers/losers (real degen movers)."""
    timeframe = "24h"
    if ctx.args and ctx.args[0].lower() in _MOVERS_TF_LABEL:
        timeframe = ctx.args[0].lower()
    label = _MOVERS_TF_LABEL.get(timeframe, "24H")
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f680 <b>Fetching {label} market movers...</b>", parse_mode=ParseMode.HTML
    )
    data = await _fetch_market_intel(
        "/api/v1/market/movers", params={"limit": 20, "timeframe": timeframe}
    )
    if not data:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    # Backend returns explicit lists for the requested window; fall back to
    # splitting the raw items when only "items" is present (older backends).
    gainers = list((data.get("top_gainers") or [])[:6])
    losers = list((data.get("top_losers") or [])[:6])
    if not gainers and not losers:
        items = (data or {}).get("items") or []
        gainers = [i for i in items if _window_change(i, timeframe) >= 0][:6]
        losers = [i for i in items if _window_change(i, timeframe) < 0][:6]

    lines = _render_movers_lines(gainers, losers, timeframe, data.get("sources"))

    rows: list[list[InlineKeyboardButton]] = []
    for item in (gainers + losers)[:8]:
        addr = item.get("address") or ""
        if not addr:
            continue
        chain = (item.get("chain") or "solana").lower()
        if (item.get("source") or "").lower() == "gmgn":
            rows.append(token_buttons(addr, chain))
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        "\U0001f4c8 Chart", url=f"https://www.coingecko.com/en/coins/{addr}"
                    )
                ]
            )
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )
