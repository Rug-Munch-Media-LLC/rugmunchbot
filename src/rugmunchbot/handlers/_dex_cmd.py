"""``/dex`` — DEX trading pairs, liquidity and volume for one token.

Split out of ``handlers/price.py`` (2026-10-24) so that module stays inside
its size ratchet; ``handlers.price`` re-exports ``cmd_dex`` unchanged, so
``bot/_entry.py`` keeps importing it from there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot.config import WEBSITE_URL
from rugmunchbot.formatters import detect_chain, fmt_chain, fmt_number, footer_links, thin_sep
from rugmunchbot.handlers._messages import (
    ACTION_CHECK_ADDRESS,
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from rugmunchbot.keyboards import back_kb
from rugmunchbot.services.pricing import resolve
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.price")


async def cmd_dex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show DEX trading pairs and liquidity for a token.

    Usage: /dex <address> [chain]
    Example: /dex 0x6982508145454Ce325dDbE47a25d4ec3d2311933
    """
    if not context.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "💱 <b>DEX Data</b>\n\n"
            + usage_hint(
                "dex",
                "address [chain]",
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                examples=("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v solana",),
                extra="Shows trading pairs, liquidity, and DEX info for a token.",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    address = context.args[0]
    chain = context.args[1].lower() if len(context.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"💱 <b>Fetching DEX data...</b>\n<code>{address}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Shared pricing service: scanner-cache snapshot → backend DataBus →
        # DexScreener. ``pairs`` are RAW DexScreener-shaped dicts — the
        # renderer below reads dexId/chainId/priceUsd/liquidity/volume/
        # pairAddress directly (same contract the old resolver returned).
        snapshot = await resolve(address=address, chain=chain)
        pairs = snapshot.pairs or []

        if not pairs:
            await msg.edit_text(
                failure_card(
                    "No Trading Pairs Found",
                    f"No DEX pairs found for <code>{address}</code>.\nThe token may not be tradable on any DEX yet.",
                    ACTION_CHECK_ADDRESS,
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        # Show top pairs (up to 5)
        lines = [
            f"💱 <b>DEX Pairs: {len(pairs)} found</b>",
            thin_sep(),
            f"<code>{address}</code>\n",
        ]

        for i, p in enumerate(pairs[:5], 1):
            dex_name = p.get("dexId", "?").capitalize()
            chain_id = p.get("chainId", chain)
            price = float(p.get("priceUsd", 0))
            liq = float(p.get("liquidity", {}).get("usd", 0))
            vol_24h = float(p.get("volume", {}).get("h24", 0))
            pair_addr = p.get("pairAddress", "")

            lines.append(
                f"{i}. <b>{dex_name}</b> ({fmt_chain(chain_id)})\n"
                f"   💵 {fmt_number(price)} | 💧 {fmt_number(liq)}\n"
                f"   📊 Vol: {fmt_number(vol_24h)} | <code>{pair_addr}</code>"
            )

        lines.append(
            f"\n🌐 <a href='{WEBSITE_URL}/markets/token/{address}'>Full market data on RugMunch.io</a>"
        )
        lines.append(footer_links())

        text = "\n".join(lines)
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                [InlineKeyboardButton("🤖 AI Analysis", callback_data=f"ai_scan_{address}")],
                [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
            ]
        )

        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
        )

    except (
        httpx.HTTPError,
        ConnectionError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
        OSError,
    ) as e:
        logger.warning("cmd_dex failed for %s: %s", address, e)
        await msg.edit_text(
            failure_card(
                "DEX Query Failed",
                f"Could not fetch DEX data for <code>{address}</code>.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
