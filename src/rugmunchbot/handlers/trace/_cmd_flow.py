#!/usr/bin/env python3
"""
RugMunchBot -- /trace command: Cross-chain fund flow tracing.

Hunter tier and above. Traces fund flows across chains, identifies
funding sources, exchange interactions, and connected wallets.
"""



from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import (
    BACKEND_URL,
    backend_headers,
    fmt_chain,
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
)
from rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from rugmunchbot.keyboards import back_kb, paywall_kb, trace_kb
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

    # Per-user spam cooldown (3s) — mirror /scan: silent drop for rapid fire.
    if check_spam(user.id):
        return

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "💰 <b>Fund Flow Tracer</b>\n\n"
            "Follow the money — trace where a wallet's funds\n"
            "came from and where they've been going.\n\n"
            + usage_hint(
                "flow",
                "wallet_address [chain]",
                "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
                examples=("vitalik.eth ethereum",),
                extra="<i>Shows funding sources, destinations, and connected wallets.</i>",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "💰 <b>Fund Flow Tracer</b>\n\n"
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
        f"💰 <b>Tracing fund flows...</b>\n<code>{short_addr(address)}</code>\n\n⏳ Analyzing wallet activity...",
        parse_mode=ParseMode.HTML,
    )

    try:
        client = get_shared_client(20)
        # Cross-chain profile via aggregator
        profile: dict[str, Any] = {}
        try:
            cc_resp = await client.get(
                f"{BACKEND_URL}/api/v1/cross-chain/{address}",
                params={"chain": chain},
                headers=backend_headers(),
                timeout=10,
            )
            if cc_resp.status_code == 200:
                profile = cc_resp.json()
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            logger.debug("cmd_flow: cross-chain profile failed: %s", exc)

        # Wallet intelligence from DataBus
        wallet_data: dict[str, Any] = {}
        try:
            wallet_resp = await client.get(
                f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens",
                headers=backend_headers(),
                timeout=10,
            )
            if wallet_resp.status_code == 200:
                wallet_data = wallet_resp.json()
        except (httpx.HTTPError, ConnectionError, TimeoutError) as exc:
            logger.debug("cmd_flow: databus wallet failed: %s", exc)

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
                lines.append(f"  🔗 <code>{short_addr(addr)}</code>{method_tag}{' | ' + conf_pct if conf_pct else ''}")
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
            failure_card(
                "Flow Trace Failed",
                "Could not trace the money flow.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


