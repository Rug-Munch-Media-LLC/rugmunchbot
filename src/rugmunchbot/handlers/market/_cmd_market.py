"""market /market — global market overview (mcap, dominance, fear/greed)."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot.bot.variants import render_variant
from rugmunchbot.config import fmt_pct
from rugmunchbot.formatters import footer_links, sep

from ._constants import MARKET_UNAVAILABLE
from ._data import _fetch_market_intel
from ._render import _fmt_usd

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


def _menu_kb() -> InlineKeyboardMarkup:
    """Shared bottom bar: back to the main menu."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")]]
    )


async def cmd_market(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /market — global market overview (mcap, volume, dominance, fear/greed, BTC/ETH)."""
    msg = await update.message.reply_text(
        render_variant(
            "market.loading",
            getattr(getattr(update, "effective_chat", None), "id", None),
            getattr(getattr(update, "effective_message", None), "message_id", None),
            subject="market overview",
        ),
        parse_mode=ParseMode.HTML,
    )  # type: ignore[union-attr]  # PTB None-able fields
    overview, macro = await asyncio.gather(
        _fetch_market_intel("/api/v1/market/overview"),
        _fetch_market_intel("/api/v1/market/macro"),
    )
    if not overview:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    lines = ["\U0001f4ca <b>Market Overview</b>", sep()]
    if overview.get("total_market_cap") is not None:
        lines.append(
            f"\U0001f310 Total Market Cap: <b>{_fmt_usd(overview.get('total_market_cap'))}</b>"
        )
    if overview.get("total_volume_24h") is not None:
        lines.append(f"\U0001f4c8 24h Volume: <b>{_fmt_usd(overview.get('total_volume_24h'))}</b>")
    if overview.get("btc_dominance") is not None:
        lines.append(f"\u20bf BTC Dominance: <b>{float(overview['btc_dominance']):.1f}%</b>")
    if overview.get("eth_dominance") is not None:
        lines.append(f"\u27e0 ETH Dominance: <b>{float(overview['eth_dominance']):.1f}%</b>")
    active = overview.get("active_cryptocurrencies")
    if active:
        with contextlib.suppress(TypeError, ValueError):
            lines.append(f"\U0001fa99 Active Coins: <b>{int(active):,}</b>")

    fg = overview.get("fear_greed") or {}
    if fg.get("value") is not None:
        fg_value = int(fg.get("value", 0))
        fg_class = fg.get("classification") or "Neutral"
        fg_emoji = (
            "\U0001f525"
            if fg_value >= 75
            else "\U0001f60d"
            if fg_value >= 60
            else "\U0001f610"
            if fg_value >= 40
            else "\U0001f631"
        )
        lines.append("")
        lines.append(f"{fg_emoji} Fear & Greed: <b>{fg_value}</b> ({fg_class})")

    if macro and macro.get("items"):
        lines.append("")
        for it in macro["items"][:2]:
            if it.get("current_price") is None:
                continue
            asset = it.get("asset", "")
            change = it.get("change_24h")
            change_txt = f" ({fmt_pct(change)})" if change is not None else ""
            lines.append(
                f"\U0001f4b0 <b>{asset}:</b> {_fmt_usd(it.get('current_price'))}{change_txt}"
            )

    lines.append(footer_links())
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=_menu_kb(),
        disable_web_page_preview=True,
    )
