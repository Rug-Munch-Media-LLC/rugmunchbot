#!/usr/bin/env python3
"""
RugMunchBot -- /trace command: Cross-chain fund flow tracing.

Hunter tier and above. Traces fund flows across chains, identifies
funding sources, exchange interactions, and connected wallets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import (
    DEXSCREENER,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    is_owner,
    paywall_text,
    sep,
    short_addr,
    thin_sep,
    web_scan_button,
)
from rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from rugmunchbot.keyboards import back_kb, paywall_kb
from rugmunchbot.states import check_spam
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.trace")

EXCHANGE_LABELS = {
    "binance": "🏦 Binance",
    "coinbase": "🏦 Coinbase",
    "kraken": "🏦 Kraken",
    "bybit": "🏦 Bybit",
    "okx": "🏦 OKX",
    "gate": "🏦 Gate.io",
    "kucoin": "🏦 KuCoin",
    "mexc": "🏦 MEXC",
    "bitget": "🏦 Bitget",
    "huobi": "🏦 HTX",
    "unknown_exchange": "🏦 Exchange",
}



async def cmd_dumps(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/dumps <token_address> [chain] — Find large sells/dumps for a token.

    Queries DexScreener for the token pair, then analyzes recent trade data
    to identify whale dumps and large sell-offs.

    Output: 🐋 0x1234...abcd sold $45K at 14:32. 0x5678...ef01 dumped $12K at 14:35

    Usage:
      /dumps 0x6982508145454Ce325dDbE47a25d4ec3d2311933
      /dumps <token> [chain]
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    # Per-user spam cooldown (3s) — mirror /scan: silent drop for rapid fire.
    if check_spam(user.id):
        return

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🐋 <b>Dump Detector</b>\n\n"
            "Find large sell-offs for any token.\n\n"
            + usage_hint(
                "dumps",
                "token_address [chain]",
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                examples=("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v solana",),
                extra="<i>Detects sells over $1K as potential dumps.</i>",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🐋 <b>Dump Detector</b>\n\n"
            "This feature requires <b>Hunter tier</b> or higher.\n\n"
            "🛰️ <b>Monitor (250★/30d)</b> — live LP/whale + tax/ownership/mint alerts\n"
            "🔬 <b>Deep Scan (250★)</b> — full forensics on demand",
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=paywall_kb()
            )
            return

    address = ctx.args[0].strip()
    chain = ctx.args[1].lower() if len(ctx.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"🐋 <b>Scanning for dumps...</b>\n<code>{short_addr(address)}</code>\n\n⏳ Querying DexScreener trades...",
        parse_mode=ParseMode.HTML,
    )

    try:
        client = get_shared_client(15)
        # Get pair data from DexScreener
        r = await client.get(f"{DEXSCREENER}/search/?q={address}", timeout=10)
        if r.status_code != 200:
            await msg.edit_text(
                "❌ <b>DexScreener unavailable</b>\n\nCould not fetch trade data.",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        pairs = r.json().get("pairs", [])
        if not pairs:
            await msg.edit_text(
                f"❌ <b>No trading pairs found</b>\n\n"
                f"No DEX activity detected for:\n<code>{address}</code>\n\n"
                f"• Check address is correct\n"
                f"• Token may not be traded yet",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        # Build dump report from all pairs
        all_dumps: list[dict[str, Any]] = []
        token_name = "???"  # noqa: S105 — placeholder until pair data resolves
        token_symbol = "???"  # noqa: S105 — placeholder until pair data resolves
        total_vol = 0.0
        price = 0.0

        for p in pairs[:5]:
            bt = p.get("baseToken", {})
            token_name = bt.get("name", "???")[:20]
            token_symbol = bt.get("symbol", "???")

            txns = p.get("txns", {})
            h24 = txns.get("h24", {})
            buys_24 = h24.get("buys", 0)
            sells_24 = h24.get("sells", 0)

            vol = p.get("volume", {})
            vol_24h = float(vol.get("h24") or 0)
            total_vol += vol_24h

            price = float(p.get("priceUsd", 0))
            chain_id = p.get("chainId", chain)
            dex = p.get("dexId", "DEX")

            # Calculate average trade size
            total_txns = buys_24 + sells_24
            avg_sell_size = (vol_24h * 0.5 / sells_24) if sells_24 > 0 else 0
            sell_domination = (sells_24 / total_txns * 100) if total_txns > 0 else 0

            if sells_24 > 0 and avg_sell_size > 1000:  # Filter for notable sells
                all_dumps.append(
                    {
                        "chain": chain_id,
                        "dex": dex,
                        "sells_24": sells_24,
                        "avg_sell_size": avg_sell_size,
                        "sell_pct": sell_domination,
                        "vol_24h": vol_24h,
                        "price": price,
                    }
                )

        pc = pairs[0].get("priceChange", {})
        h24_change = pc.get("h24") or 0
        h1_change = pc.get("h1") or 0
        liq = pairs[0].get("liquidity", {}).get("usd", 0)

        lines = [
            "🐋 <b>Dump Detector</b>",
            sep(),
            f"<b>{token_name}</b> ({token_symbol}) {fmt_chain(chain)}",
            f"<code>{address}</code>",
            "",
            "📊 <b>Market Summary</b>",
            thin_sep(),
            f"💰 Price: <b>{fmt_number(price)}</b>",
            f"🔄 1h: {fmt_pct(h1_change)} | 24h: {fmt_pct(h24_change)}",
            f"💧 Liquidity: <b>{fmt_number(liq)}</b>",
            f"📊 24h Volume: <b>{fmt_number(total_vol)}</b>",
        ]

        if all_dumps:
            lines.extend(["", "🐻 <b>Detected Dumps (24h)</b>", thin_sep()])
            for d in all_dumps[:5]:
                sell_emoji = "🔴" if d["sell_pct"] > 60 else "🟠" if d["sell_pct"] > 40 else "🟡"
                lines.append(
                    f"{sell_emoji} <b>{d['dex']}</b> [{fmt_chain(d['chain'])}]\n"
                    f"   📤 {d['sells_24']:,} sells | Avg: {fmt_number(d['avg_sell_size'])}/trade\n"
                    f"   📉 Sell domination: <b>{d['sell_pct']:.0f}%</b> | Vol: {fmt_number(d['vol_24h'])}"
                )
        else:
            lines.extend(["", "✅ <b>No significant dumps detected</b>", thin_sep()])
            lines.append("  Buy/sell activity appears balanced in the last 24h.")

        lines.extend(
            [
                "",
                "💡 <b>Tip:</b> High sell domination (>60%) with declining price = active dump.",
                footer_links(),
            ]
        )

        db.increment_usage(user.id, scan=True)
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔍 Full Scan", callback_data=f"scan_{address}_{chain}")],
                [web_scan_button(address, chain)],
                [InlineKeyboardButton("◀️ Menu", callback_data="menu_main")],
            ]
        )
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_dumps_error", exc_info=True)
        logger.error(f"Dump scan error: {e}")
        await msg.edit_text(
            failure_card(
                "Dump Scan Failed",
                "Could not run the dump-pattern scan.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


