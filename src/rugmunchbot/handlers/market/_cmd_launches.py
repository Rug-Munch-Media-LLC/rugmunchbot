"""market /launches — new token launches from the backend market router."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot.config import fmt_chain, fmt_pct
from rugmunchbot.formatters import footer_links, sep
from rugmunchbot.keyboards import token_buttons

from ._constants import MARKET_UNAVAILABLE
from ._data import _fetch_market_intel
from ._render import _change_emoji, _fmt_usd

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def cmd_launches(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /launches — new token launches from the backend market router."""
    msg = await update.message.reply_text(
        "\U0001f195 <b>Fetching new launches...</b>", parse_mode=ParseMode.HTML
    )  # type: ignore[union-attr]  # PTB None-able fields
    data = await _fetch_market_intel("/api/v1/market/launches", params={"limit": 8})
    items = (data or {}).get("items") or []
    if not items:
        await msg.edit_text(MARKET_UNAVAILABLE, parse_mode=ParseMode.HTML)
        return

    lines = ["\U0001f195 <b>New Token Launches</b>", sep()]
    for i, item in enumerate(items[:8], 1):
        symbol = (item.get("symbol") or "???").upper()[:12]
        name = (item.get("name") or symbol)[:28]
        change = item.get("change_24h") or 0
        chain_txt = fmt_chain(item.get("chain") or "solana")
        bundler = "\U0001f6a8 bundler" if item.get("bundler_detected") else ""
        addr = item.get("address") or ""
        lines.append(
            f"{i}. {_change_emoji(change)} <b>{name}</b> ({symbol}) {chain_txt}\n"
            f"   {_fmt_usd(item.get('price'))} | <b>{fmt_pct(change)}</b> | Vol: {_fmt_usd(item.get('volume_24h'))}"
            f"{f' | {bundler}' if bundler else ''}"
        )
        if addr:
            lines.append(f"   <code>{addr}</code>")
    lines.append("\n<i>Fresh launches \u2014 always /scan before buying.</i>")
    lines.append(footer_links())
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:8]:
        addr = item.get("address") or ""
        if addr:
            rows.append(token_buttons(addr, item.get("chain") or "solana"))
    rows.append([InlineKeyboardButton("\U0001f504 Refresh", callback_data="launches_refresh")])
    rows.append([InlineKeyboardButton("\U0001f3d9\ufe0f Menu", callback_data="menu_main")])
    await msg.edit_text(
        "\n".join(lines),
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(rows),
        disable_web_page_preview=True,
    )
