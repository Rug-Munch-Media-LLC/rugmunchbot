"""
RugMunchBot -- Price and DEX command handlers.

New commands:
  /price <address> [chain]    — Get token price (by contract address)
  /dex <address> [chain]      — Show DEX trading pairs & liquidity

Ported from the private monorepo (rmi-backend rugmunchbot) — import-rewritten
to ``rugmunchbot.*`` per docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md §2.3.
The live bot's top-level ``ScannerBridge`` import (private bot→scanner HTTP
bridge, checklist §2.2/§5 HARD BLOCKER) is replaced by the shared
``backend_json_get`` own-data helper — see ``_bridge_get_price``. [REVIEW]
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from rugmunchbot.config import DEXSCREENER, WEBSITE_URL
from rugmunchbot.data_resolver import backend_get_json, resolve_with_fallback
from rugmunchbot.formatters import (
    detect_chain,
    fmt_chain,
    fmt_number,
    fmt_pct,
    footer_links,
    thin_sep,
)
from rugmunchbot.keyboards import back_kb
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.registry import backend_json_get

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.price")


async def _bridge_get_price(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Own-data price lookup — ported seam for the private ScannerBridge.

    The live bot used ``app.domains.telegram.scanner_bridge.ScannerBridge``
    (private bot→scanner HTTP bridge). OSS replaces it with the shared
    ``backend_json_get`` own-data helper against the same DataBus cached
    endpoint, preserving the exact ``{price_usd, change_24h, volume_24h,
    source}`` contract the command renderer expects. [REVIEW]
    """
    body = await backend_json_get(
        "/api/v1/databus/fetch/token_price",
        params={"mint": address},
        timeout=30.0,
    )
    if body:
        pairs = body.get("pairs") or []
        if pairs:
            p = pairs[0]
            return {
                "price_usd": float(p.get("priceUsd", 0)),
                "change_24h": p.get("priceChange", {}).get("h24", 0),
                "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                "source": "databus",
            }
    return {"price_usd": 0, "change_24h": 0, "volume_24h": 0, "source": "unavailable"}


async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get token price by contract address.

    Usage: /price <address> [chain]
    Example: /price 0x6982508145454Ce325dDbE47a25d4ec3d2311933
             /price EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v solana
    """
    if not context.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "💰 <b>Token Price</b>\n\n"
            "Usage: /price <code>address</code> [chain]\n\n"
            "Examples:\n"
            "  /price <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /price <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "💡 <i>Get price data directly by contract address, not symbol.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    address = context.args[0]
    chain = context.args[1].lower() if len(context.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"💰 <b>Fetching price...</b>\n<code>{address}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        result = await _bridge_get_price(address, chain)

        if result["price_usd"] > 0:
            text = (
                f"💰 <b>Token Price</b>\n"
                f"{thin_sep()}\n"
                f"<code>{address}</code>\n"
                f"{fmt_chain(chain)}\n\n"
                f"💵 Price: <b>{fmt_number(result['price_usd'])}</b>\n"
                f"📈 24h Change: {fmt_pct(result.get('change_24h', 0))}\n"
                f"💧 Volume 24h: {fmt_number(result.get('volume_24h', 0))}\n\n"
                f"📡 Source: {result.get('source', 'unavailable')}"
                f"{footer_links()}"
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                    # [REVIEW] WEBSITE_URL is env-driven (BOT_WEBSITE_URL, empty default) —
                    # when unset the button URL is relative/broken; operator must configure it.
                    [
                        InlineKeyboardButton(
                            "📊 Charts & Analysis", url=f"{WEBSITE_URL}/markets/token/{address}"
                        )
                    ],
                    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
                ]
            )
        else:
            text = (
                f"❌ <b>Price Not Available</b>\n\n"
                f"<code>{address}</code>\n\n"
                f"No price data found for this token. It may:\n"
                f"• Not have a trading pair yet\n"
                f"• Be on an unsupported chain\n"
                f"• Have no liquidity\n\n"
                f"Try /scan for a full security analysis."
                f"{footer_links()}"
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [InlineKeyboardButton("◀️ Back", callback_data="menu_main")],
                ]
            )

        await msg.edit_text(
            text, parse_mode=ParseMode.HTML, reply_markup=kb, disable_web_page_preview=True
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, OSError) as e:
        logger.warning("cmd_price failed for %s: %s", address, e)
        await msg.edit_text(
            f"❌ <b>Price Check Failed</b>\n\n"
            f"Could not fetch price for <code>{address}</code>.\n"
            f"Please try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def _own_dex_pairs(address: str, chain: str) -> list[dict[str, Any]] | None:
    """Own-data primary: scanner cache price snapshot shaped like a DEX pair.

    Returns ``None`` when the scanner cache has no entry for this token so
    the resolver falls back to DexScreener.
    """
    chain_id = chain if chain not in ("unknown", "") else "ethereum"
    body = await backend_get_json(
        f"/api/v2/scanner/token/price/{chain_id}/{address}",
        timeout_secs=5,
    )
    if not body or not body.get("cached") or not body.get("data"):
        return None
    d = body["data"]
    price = float(d.get("price_usd") or 0)
    if price <= 0:
        return None
    return [
        {
            "dexId": "scanner",
            "chainId": d.get("chain", chain_id),
            "priceUsd": price,
            "liquidity": {"usd": float(d.get("liquidity_usd") or 0)},
            "volume": {"h24": float(d.get("volume_24h_usd") or 0)},
            "marketCap": float(d.get("market_cap_usd") or 0),
            "pairAddress": address,
            "baseToken": {"name": d.get("name", "Unknown"), "symbol": d.get("symbol", "???")},
        }
    ]


async def _external_dex_pairs(address: str) -> list[dict[str, Any]]:
    """External fallback: DexScreener token pairs (full pair list)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(f"{DEXSCREENER}/tokens/{address}")
        if resp.status_code != 200:
            return []
        data = resp.json()
        return cast("list[dict[str, Any]]", data.get("pairs", []))


async def cmd_dex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show DEX trading pairs and liquidity for a token.

    Usage: /dex <address> [chain]
    Example: /dex 0x6982508145454Ce325dDbE47a25d4ec3d2311933
    """
    if not context.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "💱 <b>DEX Data</b>\n\n"
            "Usage: /dex <code>address</code> [chain]\n\n"
            "Shows trading pairs, liquidity, and DEX info for a token.\n\n"
            "Examples:\n"
            "  /dex <code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code>\n"
            "  /dex <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana",
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
        # Own data first: scanner cache snapshot (price/mcap/liq/vol from our
        # scanners); DexScreener is only the external fallback for the full
        # pair list. DATA_SOURCE is logged by the resolver.
        pairs = await resolve_with_fallback(
            f"dex_pairs:{address[:10]}",
            lambda: _own_dex_pairs(address, chain),
            lambda: _external_dex_pairs(address),
            timeout_secs=8,
            is_valid=lambda pl: bool(pl),
        )

        if not pairs:
            await msg.edit_text(
                f"❌ <b>No Trading Pairs Found</b>\n\n"
                f"No DEX pairs found for <code>{address}</code>.\n"
                f"The token may not be tradable on any DEX yet.",
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

        # [REVIEW] WEBSITE_URL is env-driven (BOT_WEBSITE_URL, empty default);
        # the anchor text is neutral (branding scrubbed per checklist §6.15).
        lines.append(f"\n🌐 <a href='{WEBSITE_URL}/markets/token/{address}'>Full market data</a>")
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
            f"❌ <b>DEX Query Failed</b>\n\n"
            f"Could not fetch DEX data for <code>{address}</code>.\n"
            f"Error: {str(e)[:100]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
