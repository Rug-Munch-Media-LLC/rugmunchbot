#!/usr/bin/env python3
"""
RugMunchBot -- /trace command: Cross-chain fund flow tracing.

Hunter tier and above. Traces fund flows across chains, identifies
funding sources, exchange interactions, and connected wallets.
"""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from app.core.ai_guardrails import sanitize_output
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    backend_headers,
    fmt_chain,
    fmt_number,
    fmt_pct,
    risk_bar,
)
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    is_owner,
    paywall_text,
    sep,
    short_addr,
    thin_sep,
    web_scan_button,
)
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, paywall_kb, trace_kb
from app.domains.telegram.rugmunchbot.states import check_spam
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

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


# ── Own-Data fund-flow trace + AI read ─────────────────────────────────────
# /trace runs a deterministic fund-flow trace against OUR OWN ClickHouse
# chain-indexer data (wallet_flow engine: evm_transfers + wallet_labels +
# dex_events), then hands a compact summary to the snappy LLM for a
# plain-language read. Both layers fail open: ClickHouse down → fall back to
# the legacy external-API path; LLM down/timeout → deterministic flow only.

#: Hard cap for the ClickHouse flow-summary call — never stall the command.
#: Matches the engine's internal budget (10s hop queries + 30s USD oracle
#: normalization + buffer); subsequent calls hit the 5m Redis cache.
_TRACE_FLOW_TIMEOUT = 45.0
#: Hard cap for the AI flow-read LLM call (snappy tier) — never stall a reply.
_TRACE_AI_TIMEOUT = 8.0
#: Max chars of rendered LLM output (LLM text is untrusted — always escaped).
_TRACE_AI_MAX_CHARS = 900

_TRACE_READ_SYSTEM = (
    "You are the RugMunch fund-flow analyst. Write 2-3 SHORT plain-English "
    "sentences interpreting a wallet's fund-flow trace for a crypto "
    "investigator. Use ONLY the facts provided in the trace JSON — never "
    "invent amounts, addresses, exchange names or claims. Describe how the "
    "funds moved (e.g. funded by X, routed to Y, ending at exchange Z), "
    "where they ended up (exchange vs non-exchange share), and whether the "
    "pattern suggests cashing out, accumulation, or wash-like round-tripping. "
    "No markdown, no emojis, no headers, no apologies."
)


async def _fetch_clickhouse_flow(address: str, chain: str) -> dict[str, Any] | None:
    """Best-effort fund-flow summary from our own ClickHouse chain-indexer data.

    Wraps ``wallet_flow.build_flow_summary`` (hop-1/hop-2 evm_transfers,
    wallet_labels, dex_events). Never raises — returns ``None`` on any
    failure so /trace fails open to the legacy external-API path.
    """
    try:
        from app.domains.scanner.engines.wallet_flow import build_flow_summary

        summary = await asyncio.wait_for(
            build_flow_summary(address, chain, hops=2),
            timeout=_TRACE_FLOW_TIMEOUT,
        )
        if not summary or not isinstance(summary, dict):
            return None
        if int(summary.get("total_tx_count") or 0) == 0:
            return None
        return summary
    except Exception as exc:  # fail-open boundary for /trace
        logger.debug("trace_clickhouse_flow_failed: %s", exc)
        return None


async def _render_ai_flow_read(flow_summary: dict[str, Any]) -> str:
    """Ask the snappy LLM for a plain-language read of the deterministic flow.

    Passes the compact JSON summary (only measured facts) to
    ``chat_with_fallback(messages, "snappy")`` bounded by ``_TRACE_AI_TIMEOUT``.
    Fail-open: timeout, rate-limit, blank answer all return "".
    """
    if not flow_summary:
        return ""
    import json

    from app.llm_config import chat_with_fallback

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _TRACE_READ_SYSTEM},
        {
            "role": "user",
            "content": (
                "Wallet fund-flow trace (JSON, only facts we measured):\n"
                f"{json.dumps(flow_summary, default=str)[:1000]}\n\n"
                "Write the flow read now."
            ),
        },
    ]
    try:
        text = await asyncio.wait_for(chat_with_fallback(messages, "snappy"), timeout=_TRACE_AI_TIMEOUT)
    except Exception as e:  # fail-open boundary for the AI read
        logger.warning("trace AI read failed: %s", e)
        text = ""
    text = (text or "").strip()
    if not text:
        return ""
    return f"🧠 <b>AI Flow Read</b>\n{html.escape(sanitize_output(text))[:_TRACE_AI_MAX_CHARS]}"


def _fmt_counterparty(cp: dict[str, Any], usd_unavailable: bool) -> str:
    """One line for a top counterparty: addr + type emoji + label + $/tx."""
    addr = short_addr(str(cp.get("address") or ""))
    ctype = str(cp.get("type") or "")
    label = str(cp.get("label") or "")
    usd = float(cp.get("usd") or 0)
    tx = int(cp.get("tx_count") or 0)
    emoji = {
        "CEX": "🏦",
        "DEX": "💱",
        "bridge": "🌉",
        "mixer": "🌪️",
        "contract": "📄",
    }.get(ctype, "")
    parts = [f"<code>{addr}</code>"]
    if emoji:
        parts.append(emoji)
    if label:
        parts.append(label)
    if usd_unavailable or usd <= 0:
        parts.append(f"{tx} tx")
    else:
        parts.append(f"${fmt_number(usd)} · {tx} tx")
    return "  " + " ".join(parts)


def _render_flow_block(summary: dict[str, Any]) -> list[str]:
    """Deterministic 'Fund Flow (own chain data)' section from the summary."""
    d = thin_sep()
    usd_unavailable = bool(summary.get("usd_unavailable"))
    in_tx = int(summary.get("in_tx_count") or 0)
    out_tx = int(summary.get("out_tx_count") or 0)

    if usd_unavailable:
        flow_line = f"  ⬇️ In: <b>{in_tx} tx</b>  ⬆️ Out: <b>{out_tx} tx</b> (USD n/a)"
    else:
        flow_line = (
            f"  ⬇️ In: <b>{fmt_number(float(summary.get('in_usd') or 0))}</b> ({in_tx} tx)  "
            f"⬆️ Out: <b>{fmt_number(float(summary.get('out_usd') or 0))}</b> ({out_tx} tx)"
        )

    exch = summary.get("exchange") or {}
    if usd_unavailable:
        share_pct = float(exch.get("share_tx_pct") or 0)
        share_label = f"{share_pct:.0f}% (by tx)"
    else:
        share_pct = float(exch.get("share_pct") or 0)
        share_label = f"{share_pct:.0f}%"

    lines = [
        "🧭 <b>Fund Flow (own chain data)</b>",
        d,
        flow_line,
        f"  🏦 Exchange share: <b>{share_label}</b> ({int(exch.get('tx_count') or 0)} exchange tx)",
    ]

    top_in = summary.get("counterparty_in") or []
    if top_in:
        lines.extend(["", "  ⬇️ <b>Top senders</b>"])
        for cp in top_in[:3]:
            lines.append(_fmt_counterparty(cp, usd_unavailable))

    top_out = summary.get("counterparty_out") or []
    if top_out:
        lines.extend(["", "  ⬆️ <b>Top recipients</b>"])
        for cp in top_out[:3]:
            lines.append(_fmt_counterparty(cp, usd_unavailable))

    wash = summary.get("wash") or {}
    if wash.get("signal"):
        lines.extend(
            [
                "",
                f"  🔁 <b>Wash pattern</b>: {int(wash.get('reciprocal_pairs') or 0)} round-trip "
                f"pair(s), {int(wash.get('tx_count') or 0)} tx",
            ]
        )
    return lines


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


def register_trace_commands(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register /dumps and /flow on the bot application.

    /trace is registered separately by bot.py (via ``_reg``); this helper
    wires the two transaction-tracing commands ported from the legacy
    rugmunchbot repo. Call from bot.py after the other command registrations:

        register_trace_commands(app)
    """
    app.add_handler(CommandHandler("dumps", cmd_dumps))
    app.add_handler(CommandHandler("flow", cmd_flow))
