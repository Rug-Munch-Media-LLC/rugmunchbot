#!/usr/bin/env python3
"""
RugMunchBot -- /trace command: Cross-chain fund flow tracing.

Hunter tier and above. Traces fund flows across chains, identifies
funding sources, exchange interactions, and connected wallets.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode

from rugmunchbot import db
from rugmunchbot.config import (
    BACKEND_URL,
    backend_headers,
    fmt_number,
)
from rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    is_owner,
    paywall_text,
    sep,
    thin_sep,
)
from rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from rugmunchbot.handlers.trace._flow import (
    _fetch_clickhouse_flow,
    _render_ai_flow_read,
    _render_flow_block,
)
from rugmunchbot.keyboards import back_kb, paywall_kb, trace_kb
from rugmunchbot.states import check_spam
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client

from ._common import _label_exchange

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





async def cmd_trace(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Cross-chain fund flow tracing.

    Usage: /trace <address>
    Hunter tier and above.
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
            "🔍 <b>Cross-Chain Fund Trace</b>\n\n"
            + usage_hint(
                "trace",
                "address",
                "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
                extra=(
                    "Traces fund flows across chains, identifies funding sources,\n"
                    "exchange interactions, and connected wallets.\n\n"
                    "🎯 <b>Hunter tier</b> or higher required."
                ),
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🔍 <b>Cross-Chain Fund Trace</b>\n\n"
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

    address = ctx.args[0]
    chain = ctx.args[1].lower() if len(ctx.args) > 1 else detect_chain(address)

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"🔍 <b>Starting fund trace...</b>\n<code>{address}</code>\n\n"
        f"⏳ Tracing across chains and connected wallets...",
        parse_mode=ParseMode.HTML,
    )

    try:
        client = get_shared_client(30)
        data: dict[str, Any] = {}
        cross_chain: list[Any] = []

        async def _fetch_wallet() -> dict[str, Any]:
            try:
                wallet_resp = await client.get(
                    f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens",
                    headers=backend_headers(),
                    timeout=15,
                )
                if wallet_resp.status_code == 200:
                    return wallet_resp.json()
            except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
                logger.warning(f"trace wallet fetch failed: {e}")
            return {}

        async def _fetch_cross_chain() -> list[Any]:
            try:
                cc_resp = await client.get(
                    f"{BACKEND_URL}/api/v1/cross-chain/{address}",
                    params={"chain": chain},
                    headers=backend_headers(),
                    timeout=10,
                )
                if cc_resp.status_code == 200:
                    profile = cc_resp.json()
                    primary = profile.get("primary_chain", chain)
                    return [
                        {
                            "source_chain": primary,
                            "dest_chain": m.get("chain", ""),
                            "address": m.get("address", ""),
                            "confidence": m.get("confidence", 0),
                        }
                        for m in profile.get("addresses", [])
                    ]
            except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
                logger.warning("trace holders fetch failed: %s", e)
            return []

        # Own ClickHouse flow summary + wallet tokens + cross-chain run
        # concurrently (previously two external fetches — up to 25s worst
        # case). The ClickHouse flow is the primary fund trace; the external
        # fetches enrich it (funding source, connected wallets). Fail-open:
        # any missing piece is skipped, never fatal.
        flow_summary, data, cross_chain = await asyncio.gather(
            _fetch_clickhouse_flow(address, chain),
            _fetch_wallet(),
            _fetch_cross_chain(),
            return_exceptions=True,
        )
        if isinstance(flow_summary, BaseException) or not isinstance(flow_summary, dict):
            flow_summary = None
        if not isinstance(data, dict):
            data = {}
        if not isinstance(cross_chain, list):
            cross_chain = []

        # AI interpretation of the deterministic flow (bounded 8s, fail-open).
        ai_read = await _render_ai_flow_read(flow_summary) if flow_summary else ""

        s = sep()
        d = thin_sep()

        lines = [
            "🔍 <b>Cross-Chain Fund Trace</b>",
            s,
            f"<code>{address}</code>",
            f"⛓️ Chain: {chain}",
            "",
        ]

        # Own-Data fund-flow trace (ClickHouse) + AI read — primary content.
        if flow_summary:
            lines.extend(_render_flow_block(flow_summary))
            lines.append("")
        if ai_read:
            lines.extend([ai_read, ""])

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
                    f"  🌐 {src} → {dst}{' ($' + fmt_number(amt) + ')' if amt else ''}{' | ' + ts[:10] if ts else ''}"
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
                f"  ⏳ Wallet Age: <b>{age_days} days</b>" if age_days else "  ⏳ Wallet Age: Unknown",
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
            failure_card(
                "Trace Failed",
                f"Could not trace funds for <code>{address}</code>.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


