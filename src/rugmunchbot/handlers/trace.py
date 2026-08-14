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
from telegram.ext import CommandHandler

from rugmunchbot import db
from rugmunchbot.config import (
    DEXSCREENER,
    fmt_chain,
    fmt_number,
    fmt_pct,
    risk_bar,
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
from rugmunchbot.keyboards import back_kb, paywall_kb, trace_kb
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.registry import backend_json_get

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import Application, ContextTypes

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


def _label_exchange(name: str) -> str:
    """Return emoji-labeled exchange name."""
    key = name.lower().replace(".", "").replace(" ", "_")
    for k, v in EXCHANGE_LABELS.items():
        if k in key or key in k:
            return v
    return f"🏦 {name}"


async def cmd_trace(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Cross-chain fund flow tracing.

    Usage: /trace <address>
    Hunter tier and above.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🔍 <b>Cross-Chain Fund Trace</b>\n\n"
            "Usage: /trace <code>address</code>\n\n"
            "Traces fund flows across chains, identifies funding sources,\n"
            "exchange interactions, and connected wallets.\n\n"
            "<b>Example:</b>\n"
            "  /trace <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "🎯 <b>Hunter tier</b> or higher required.",
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🔍 <b>Cross-Chain Fund Trace</b>\n\n"
            "This feature requires <b>Hunter tier</b> or higher.\n\n"
            # [REVIEW] USD tier prices scrubbed (private billing — checklist §6 #17).
            "🎯 <b>Hunter tier</b> — Fund flow tracing\n"
            "👑 <b>Pro tier</b> — Deep chain analytics + clustering",
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

    address = ctx.args[0]
    chain = ctx.args[1].lower() if len(ctx.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"🔍 <b>Starting fund trace...</b>\n<code>{address}</code>\n\n"
        f"⏳ Tracing across chains and connected wallets...",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Fetch all available intelligence from backend (operator-supplied
        # RMI_BACKEND_URL; backend_json_get returns None on failure).
        data: dict[str, Any] = {}
        wallet_data = await backend_json_get(f"/api/v1/databus/wallet/{address}/tokens", timeout=15)
        if wallet_data:
            data = wallet_data

        # Fetch additional cross-chain data
        cross_chain: list[Any] = []
        profile = await backend_json_get(
            f"/api/v1/cross-chain/{address}", params={"chain": chain}, timeout=10
        )
        if profile:
            primary = profile.get("primary_chain", chain)
            cross_chain = [
                {
                    "source_chain": primary,
                    "dest_chain": m.get("chain", ""),
                    "address": m.get("address", ""),
                    "confidence": m.get("confidence", 0),
                }
                for m in profile.get("addresses", [])
            ]

        s = sep()
        d = thin_sep()

        lines = [
            "🔍 <b>Cross-Chain Fund Trace</b>",
            s,
            f"<code>{address}</code>",
            f"⛓️ Chain: {chain}",
            "",
        ]

        # Funding Source
        funding = data.get("funding_source")
        if funding:
            lines.extend(
                [
                    "<b>💰 Funding Source</b>",
                    d,
                    f"  {funding}",
                    "",
                ]
            )
        elif data.get("is_exchange"):
            lines.extend(
                [
                    "💰 <b>Funding Source</b>",
                    d,
                    "  🏦 Exchange-funded wallet",
                    "",
                ]
            )
        else:
            # Try to trace back first funder
            lines.extend(
                [
                    "💰 <b>Funding Source</b>",
                    d,
                    "  ❓ Unknown — wallet may be self-funded or untraceable",
                    "",
                ]
            )

        # Connected Wallets
        connected = data.get("connected_wallets", [])
        if connected:
            lines.extend(
                [
                    "🔗 <b>Connected Wallets</b>",
                    d,
                ]
            )
            for _i, w in enumerate(connected[:8], 1):
                label = ""
                if isinstance(w, dict):
                    addr_w = w.get("address", str(w))
                    label = w.get("label", "")
                    chain_w = w.get("chain", "")
                    conn_type = w.get("type", "linked")
                else:
                    addr_w = str(w)
                    chain_w = ""
                    conn_type = "linked"

                conn_emoji = {
                    "funded": "⬅️",
                    "funded_by": "➡️",
                    "linked": "🔗",
                    "same_owner": "👤",
                    "exchange": "🏦",
                    "contract": "📄",
                }.get(conn_type, "🔗")
                chain_tag = f" ({chain_w})" if chain_w else ""
                label_tag = f" — {label}" if label else ""
                lines.append(f"  {conn_emoji} <code>{addr_w}</code>{chain_tag}{label_tag}")
            if len(connected) > 8:
                lines.append(f"  ... and {len(connected) - 8} more")
            lines.append("")

        # Cross-Chain Connections
        if cross_chain:
            lines.extend(
                [
                    "🌉 <b>Cross-Chain Activity</b>",
                    d,
                ]
            )
            for conn in cross_chain[:6]:
                src = conn.get("source_chain", "")
                dst = conn.get("dest_chain", "")
                amt = conn.get("amount_usd", 0)
                ts = conn.get("timestamp", "")
                lines.append(
                    f"  🌐 {src} → {dst}"
                    f"{' ($' + fmt_number(amt) + ')' if amt else ''}"
                    f"{' | ' + ts[:10] if ts else ''}"
                )
            if len(cross_chain) > 6:
                lines.append(f"  ... and {len(cross_chain) - 6} more")
            lines.append("")

        # Exchange Interactions
        exchanges = data.get("exchange_interactions", data.get("exchanges", []))
        if exchanges:
            lines.extend(
                [
                    "🏦 <b>Exchange Interactions</b>",
                    d,
                ]
            )
            for ex in exchanges[:5]:
                if isinstance(ex, dict):
                    name = ex.get("name", str(ex))
                    direction = ex.get("direction", "interacted")
                    amount = ex.get("amount_usd", 0)
                else:
                    name = str(ex)
                    direction = "interacted"
                    amount = 0
                direction_emoji = {
                    "deposit": "⬆️",
                    "withdrawal": "⬇️",
                    "interacted": "🔄",
                }.get(direction.lower(), "🔄")
                amt_str = f" ${fmt_number(amount)}" if amount else ""
                lines.append(f"  {direction_emoji} {_label_exchange(name)}{amt_str}")
            lines.append("")

        # Wallet Activity Summary
        tx_count = data.get("tx_count", 0)
        balance = data.get("balance_usd", 0)
        age_days = data.get("wallet_age_days", 0)
        pnl = data.get("pnl_estimate")

        lines.extend(
            [
                "<b>📊 Wallet Summary</b>",
                d,
                f"  💰 Balance: <b>{fmt_number(balance)}</b>",
                f"  📝 Transactions: <b>{tx_count:,}</b>" if tx_count else "  📝 Transactions: N/A",
                f"  ⏳ Wallet Age: <b>{age_days} days</b>"
                if age_days
                else "  ⏳ Wallet Age: Unknown",
            ]
        )

        if pnl is not None:
            pnl_emoji = "📈" if pnl >= 0 else "📉"
            lines.append(f"  {pnl_emoji} Est. P&L: <b>{fmt_number(pnl)}</b>")

        if data.get("flags"):
            lines.extend(["", "⚠️ <b>Flags</b>", d])
            for flag in data["flags"][:5]:
                lines.append(f"  🔴 {flag}")

        lines.append(footer_links())

        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, address, chain, "trace", 0)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=trace_kb(address, chain),
            disable_web_page_preview=True,
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_trace_error", exc_info=True)
        logger.error(f"Trace error: {e}")
        await msg.edit_text(
            f"❌ <b>Trace Failed</b>\n\nCould not trace funds for <code>{address}</code>.\nError: {str(e)[:150]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


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

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🐋 <b>Dump Detector</b>\n\n"
            "Find large sell-offs for any token.\n\n"
            "<b>Usage:</b> /dumps <code>token_address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /dumps <code>0x6982...1933</code>\n"
            "  /dumps <code>EPjFWdd5...Dt1v</code> solana\n\n"
            "<i>Detects sells over $1K as potential dumps.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🐋 <b>Dump Detector</b>\n\n"
            "This feature requires <b>Hunter tier</b> or higher.\n\n"
            # [REVIEW] USD tier prices scrubbed (private billing — checklist §6 #17).
            "🎯 <b>Hunter tier</b> — Fund flow tracing\n"
            "👑 <b>Pro tier</b> — Deep chain analytics + clustering",
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
        async with httpx.AsyncClient(timeout=15) as client:
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
                    sell_emoji = (
                        "🔴" if d["sell_pct"] > 60 else "🟠" if d["sell_pct"] > 40 else "🟡"
                    )
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
            f"❌ <b>Dump Scan Failed</b>\n\n{str(e)[:150]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


async def cmd_flow(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/flow <wallet_address> [chain] — Follow the money: fund sources and destinations.

    Uses the backend cross-chain aggregator and wallet intelligence to trace
    where funds came from and where they went. Shows:
      - Cross-chain profile (connected wallets across chains)
      - Funding source
      - Connected wallets / linked addresses
      - Risk assessment

    Usage:
      /flow 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045
      /flow <wallet> [chain]
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "💰 <b>Fund Flow Tracer</b>\n\n"
            "Follow the money — trace where a wallet's funds\n"
            "came from and where they've been going.\n\n"
            "<b>Usage:</b> /flow <code>wallet_address</code> [chain]\n\n"
            "<b>Examples:</b>\n"
            "  /flow <code>0xd8dA6B...6045</code>\n"
            "  /flow <code>vitalik.eth</code> ethereum\n\n"
            "<i>Shows funding sources, destinations, and connected wallets.</i>",
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "💰 <b>Fund Flow Tracer</b>\n\n"
            "This feature requires <b>Hunter tier</b> or higher.\n\n"
            # [REVIEW] USD tier prices scrubbed (private billing — checklist §6 #17).
            "🎯 <b>Hunter tier</b> — Fund flow tracing\n"
            "👑 <b>Pro tier</b> — Deep chain analytics + clustering",
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
        f"💰 <b>Tracing fund flows...</b>\n<code>{short_addr(address)}</code>\n\n⏳ Analyzing wallet activity...",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Cross-chain profile via aggregator (operator-supplied backend)
        profile: dict[str, Any] = {}
        cc_data = await backend_json_get(
            f"/api/v1/cross-chain/{address}", params={"chain": chain}, timeout=10
        )
        if cc_data:
            profile = cc_data

        # Wallet intelligence from DataBus
        wallet_data: dict[str, Any] = {}
        w_data = await backend_json_get(f"/api/v1/databus/wallet/{address}/tokens", timeout=10)
        if w_data:
            wallet_data = w_data

        if not profile and not wallet_data:
            await msg.edit_text(
                f"❌ <b>No wallet data found</b>\n\n"
                f"No on-chain activity detected for:\n<code>{address}</code>\n\n"
                f"• Check the address is correct\n"
                f"• The wallet may have zero transactions",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        primary_chain = profile.get("primary_chain", chain)
        connected = profile.get("addresses", [])
        label = profile.get("label", "")

        lines = [
            "💰 <b>Fund Flow Trace</b>",
            sep(),
            f"<code>{address}</code>",
            fmt_chain(primary_chain),
            "",
        ]

        if label:
            lines.extend(["🏷️ <b>Label:</b>", thin_sep(), f"  {label}", ""])

        # Cross-Chain Connected Wallets
        if connected:
            lines.extend(
                [
                    "🌉 <b>Cross-Chain Wallets</b>",
                    thin_sep(),
                ]
            )
            for conn in connected[:8]:
                addr = conn.get("address", "")
                conf = conn.get("confidence", 0)
                method = conn.get("method", "")
                conf_pct = f"{conf * 100:.0f}%" if isinstance(conf, (int, float)) else ""
                method_tag = f" ({method})" if method and method != "Direct lookup" else ""
                lines.append(
                    f"  🔗 <code>{short_addr(addr)}</code>{method_tag}{' | ' + conf_pct if conf_pct else ''}"
                )
            if len(connected) > 8:
                lines.append(f"  ... and {len(connected) - 8} more")
            lines.append("")

        # Funding source from wallet intelligence
        funding = wallet_data.get("funding_source") if isinstance(wallet_data, dict) else None
        if funding:
            lines.extend(
                [
                    "🏦 <b>Funding Source</b>",
                    thin_sep(),
                    f"  {str(funding)[:200]}",
                    "",
                ]
            )

        # Risk assessment
        risk = profile.get("risk_score", 0) or 0
        if risk:
            lines.extend(
                [
                    "⚠️ <b>Risk Assessment</b>",
                    thin_sep(),
                    f"  {risk_bar(float(risk))}",
                    "",
                ]
            )

        # Chains covered
        chains = profile.get("chains", [])
        if chains:
            lines.extend(
                [
                    "⛓️ <b>Chains Detected</b>",
                    thin_sep(),
                    "  " + " · ".join(fmt_chain(c) for c in chains[:6]),
                    "",
                ]
            )

        lines.append(footer_links())

        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=trace_kb(address, chain),
            disable_web_page_preview=True,
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_flow_error", exc_info=True)
        logger.error(f"Flow error: {e}")
        await msg.edit_text(
            f"❌ <b>Flow Trace Failed</b>\n\n{str(e)[:150]}",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


def register_trace_commands(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register /dumps and /flow on the bot application.

    /trace is registered separately by bot.py (via ``_reg``); this helper
    wires the two transaction-tracing commands ported from the legacy
    rugmunchbot repo. Call from bot.py after the other command registrations:

        register_trace_commands(app)
    """
    app.add_handler(CommandHandler("dumps", cmd_dumps))
    app.add_handler(CommandHandler("flow", cmd_flow))
