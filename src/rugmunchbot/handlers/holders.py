#!/usr/bin/env python3
"""
RugMunchBot -- /holders command: Top holders + concentration analysis.

Scout tier and above. Shows holder distribution, concentration risk,
whale alerts, and smart money detection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    backend_headers,
)
from app.domains.telegram.rugmunchbot.data_resolver import backend_get_json, resolve_with_fallback
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    is_owner,
    paywall_text,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, holders_kb, paywall_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.holders")

HOLDER_THRESHOLDS = {
    "whale": 5.0,  # >5% is whale territory
    "concentrated": 2.0,  # >2% is concerning
    "notable": 1.0,  # >1% is notable
}


def _holder_risk(highest_pct: float, top10_sum: float, total_holders: int) -> tuple[str, list[str]]:
    """Calculate concentration risk from holder data."""
    flags = []
    if highest_pct > 20:
        risk = "critical"
        flags.append("🔴 Single wallet holds >20% — extreme centralization risk")
    elif highest_pct > 10:
        risk = "high"
        flags.append("🟠 Top holder >10% — high concentration")
    elif highest_pct > 5:
        risk = "medium"
        flags.append("🟡 Top holder >5% — moderate concentration")
    else:
        risk = "low"

    if top10_sum > 60:
        flags.append("🔴 Top 10 hold >60% of supply — rug risk")
    elif top10_sum > 40:
        flags.append("🟠 Top 10 hold >40% — high concentration")
    elif top10_sum > 25:
        flags.append("🟡 Top 10 hold >25% — moderate concentration")
    else:
        flags.append("✅ Top 10 hold <25% — good distribution")

    if total_holders < 100:
        flags.append("🔴 Very few holders — illiquid / new token risk")
    elif total_holders < 500:
        flags.append("🟡 Low holder count (<500)")

    return risk, flags


async def cmd_holders(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show top holders and concentration analysis.

    Usage: /holders <address> [chain]
    Scout tier and above.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "👥 <b>Holder Analysis</b>\n\n"
            + usage_hint(
                "holders",
                "address [chain]",
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                examples=("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",),
                extra="Shows top holders, concentration risk, whale detection.\n🔍 <b>Scout tier</b> or higher required.",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier not in ("scout", "hunter", "pro") and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "👥 <b>Holder Analysis</b>\n\n"
            "This feature requires <b>Scout tier</b> or higher.\n\n"
            "🛰️ <b>Monitor (250★/30d)</b> — live LP/whale + tax/ownership/mint alerts · 🔬 <b>Deep Scan (250★)</b> — full forensics",
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
        f"👥 <b>Fetching holder data...</b>\n<code>{address}</code>",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Fetch token info + top pairs — own data first (scanner cache /
        # backend databus), DexScreener only as external fallback.
        # DATA_SOURCE is logged by the resolver.
        token_name = "Unknown"  # noqa: S105 — config default
        token_symbol = "???"  # noqa: S105 — config default
        dex_pairs: list[dict[str, Any]] = []

        async def _own_token_info() -> list[dict[str, Any]] | None:
            chain_id = chain if chain not in ("unknown", "") else detect_chain(address)
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
                    "baseToken": {
                        "name": d.get("name", "Unknown"),
                        "symbol": d.get("symbol", "???"),
                    },
                    "chainId": d.get("chain", chain_id),
                    "priceUsd": price,
                    "pairAddress": address,
                }
            ]

        async def _external_token_info() -> list[dict[str, Any]]:
            body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=20)
            if not body:
                return []
            return body.get("pairs", [])

        dex_pairs = await resolve_with_fallback(
            f"holder_token_info:{address[:10]}",
            _own_token_info,
            _external_token_info,
            timeout_secs=8,
            is_valid=lambda pl: bool(pl),
        )
        if dex_pairs:
            p = dex_pairs[0]
            token_name = p.get("baseToken", {}).get("name", "Unknown")
            token_symbol = p.get("baseToken", {}).get("symbol", "???")
            chain = p.get("chainId", chain)

        # Get backend holder data
        backend_holders = []
        backend_data: dict[str, Any] = {}
        try:
            fetched = await http_get_json(
                f"{BACKEND_URL}/api/v1/databus/token/{address}",
                headers=backend_headers(),
                timeout=10,
            )
            if fetched:
                backend_data = fetched
            if backend_data:
                backend_holders = backend_data.get("top_holders", [])
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.warning("holders backend fetch failed: %s", e)

        # Build holder list from available data sources
        top_holders = []
        if backend_holders:
            top_holders = backend_holders[:20]

        # Build report
        s = sep()
        d = thin_sep()

        lines = [
            "👥 <b>Holder Analysis</b>",
            s,
            f"<b>{token_name}</b> ({token_symbol})",
            f"<code>{address}</code>",
            "",
        ]

        if top_holders:
            sum(h.get("pct", 0) for h in top_holders)
            highest_pct = top_holders[0].get("pct", 0) if top_holders else 0
            top10_sum = sum(h.get("pct", 0) for h in top_holders[:10])
            total_holders = len(top_holders)

            risk, flags = _holder_risk(highest_pct, top10_sum, total_holders)

            # Concentration overview
            risk_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "✅"}.get(risk, "⚪")
            lines.extend(
                [
                    f"<b>Concentration Risk:</b> {risk_emoji} {risk.upper()}",
                    d,
                    f"🐳 Top 1: <b>{highest_pct:.1f}%</b>",
                    f"📊 Top 10: <b>{top10_sum:.1f}%</b>",
                    f"👥 Total tracked: <b>{total_holders}</b>",
                    "",
                    "<b>🚩 Flags</b>",
                    d,
                ]
            )
            lines.extend(flags)
            lines.extend(["", "<b>👑 Top Holders</b>", d])

            # Show top 15 holders
            for i, h in enumerate(top_holders[:15], 1):
                pct = h.get("pct", 0)
                addr_h = h.get("address", "???")
                tag = f" [{h.get('tag', '')}]" if h.get("tag") else ""
                cf = " 📄" if h.get("is_contract") else ""
                whale = " 🐳" if pct >= HOLDER_THRESHOLDS["whale"] else ""

                # Progress bar for visual
                bar_len = 10
                filled = min(int((pct / 20) * bar_len), bar_len)
                bar = "🟥" * filled + "⬜" * (bar_len - filled)

                lines.append(f"{i}. <code>{addr_h}</code>{tag}{cf}{whale}\n   {bar} {pct:.2f}%")

            # Distribution chart
            lines.extend(
                [
                    "",
                    "<b>📊 Distribution Summary</b>",
                    d,
                ]
            )
            categories = [
                (">10%", sum(h.get("pct", 0) for h in top_holders if h.get("pct", 0) >= 10)),
                ("5-10%", sum(h.get("pct", 0) for h in top_holders if 5 <= h.get("pct", 0) < 10)),
                ("1-5%", sum(h.get("pct", 0) for h in top_holders if 1 <= h.get("pct", 0) < 5)),
                ("<1%", sum(h.get("pct", 0) for h in top_holders if h.get("pct", 0) < 1)),
            ]
            for label, pct in categories:
                bar_len = 10
                filled = min(int((pct / 50) * bar_len), bar_len)
                bar = "🟧" * filled + "⬜" * (bar_len - filled)
                lines.append(f"  {label}: {bar} {pct:.1f}%")

            # Smart money detection (Hunter+)
            if tier in ("hunter", "pro"):
                try:
                    dev_wallet = backend_data.get("dev_wallet")
                    if dev_wallet:
                        lines.extend(
                            [
                                "",
                                "🎯 <b>Smart Money Detection</b>",
                                d,
                                f"  👨‍💻 Dev Wallet: <code>{dev_wallet}</code>",
                            ]
                        )
                    if backend_data.get("bundle_detected"):
                        lines.append("  📦 Bundle activity detected — coordinated buys")
                except (KeyError, TypeError) as e:
                    logger.debug("holders line render failed: %s", e)

        else:
            # Fallback: show what we can from DEX data
            if dex_pairs:
                pair = dex_pairs[0]
                txns = pair.get("txns", {})
                h24_buys = txns.get("h24", {}).get("buys", 0)
                h24_sells = txns.get("h24", {}).get("sells", 0)
                h24_total = h24_buys + h24_sells

                lines.extend(
                    [
                        "<b>Holder data unavailable</b> — showing trading activity instead",
                        d,
                        f"📊 24h Traders: <b>{h24_total:,}</b>",
                        f"  🔵 Buyers: {h24_buys:,}",
                        f"  🔴 Sellers: {h24_sells:,}",
                        "",
                        "💡 Holder data unavailable for this token.",
                        "Use /scan for full security analysis.",
                    ]
                )
            else:
                lines.append("❌ No holder or trading data found for this address.")

        lines.append(footer_links())

        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, address, chain, "holders", 0)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=holders_kb(address, chain),
            disable_web_page_preview=True,
        )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_holders_error", exc_info=True)
        logger.error(f"Holder analysis error: {e}")
        await msg.edit_text(
            failure_card(
                "Holder Analysis Failed",
                f"Could not fetch holder data for <code>{address}</code>.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
