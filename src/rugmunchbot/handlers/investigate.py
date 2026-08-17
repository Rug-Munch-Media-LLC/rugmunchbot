#!/usr/bin/env python3
"""
RugMunchBot -- /investigate command: Deep investigation report.

Pro tier. Combines full scanner data + wallet forensics + holder analysis
+ cross-chain tracing into a comprehensive investigation report.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    BACKEND_URL,
    WEBSITE_URL,
    backend_headers,
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
    thin_sep,
)
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
    usage_hint,
)
from app.domains.telegram.rugmunchbot.keyboards import back_kb, investigate_kb, paywall_kb
from app.domains.telegram.rugmunchbot.scanner import scan_token, scan_token_v2, v2_token_to_legacy
from app.domains.telegram.rugmunchbot.states import check_spam
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.investigate")

_VERDICT_EMOJIS = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🟢",
    "SAFE": "✅",
}


def _determine_verdict(scan_result: dict[str, Any], wallet_data: dict[str, Any]) -> tuple[str, str]:
    """Determine overall investigation verdict."""
    risk = scan_result.get("risk_score") or 0
    threats = len(scan_result.get("threats") or [])
    wallet_flags = len((wallet_data or {}).get("flags") or [])
    bundle = bool(scan_result.get("bundle_detected"))
    fresh = (scan_result.get("fresh_wallet_pct") or 0) > 50
    honeypot = bool(scan_result.get("honeypot"))
    low_liq = (scan_result.get("liquidity") or 0) < 1000 and (scan_result.get("mcap") or 0) > 10000

    score = risk
    if honeypot:
        score = 100
    if bundle:
        score += 15
    if fresh:
        score += 10
    if low_liq:
        score += 10
    if wallet_flags > 2:
        score += 10

    score = min(score, 100)

    if score >= 80:
        return "CRITICAL", f"DANGER: {threats} threats, {wallet_flags} wallet flags"
    elif score >= 60:
        return "HIGH", f"RISKY: {threats} threats found"
    elif score >= 40:
        return "MEDIUM", f"CAUTION: {threats} issues detected"
    elif score >= 20:
        return "LOW", "LOW RISK: Minor concerns"
    else:
        return "SAFE", "Clean — no significant risks detected"


async def _run_scan(address: str, chain: str) -> dict[str, Any]:
    """Security scan — v2 explainable first, legacy fallback."""
    scan_v2 = await scan_token_v2(address, chain)
    if scan_v2 is not None:
        scan_result = v2_token_to_legacy(scan_v2)
        if chain:
            scan_result["chain"] = chain
        return scan_result
    return await scan_token(address, chain)


async def _fetch_wallet_tokens(client: httpx.AsyncClient, address: str) -> dict[str, Any]:
    """Fetch wallet token holdings from the backend (best-effort)."""
    try:
        wallet_resp = await client.get(
            f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens",
            headers=backend_headers(),
            timeout=10,
        )
        if wallet_resp.status_code == 200:
            return wallet_resp.json()
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.warning("investigate backend fetch failed: %s", e)
    return {}


async def _fetch_cross_chain(client: httpx.AsyncClient, address: str, chain: str) -> list[Any]:
    """Fetch cross-chain profile from the backend (best-effort)."""
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
        logger.warning("investigate history fetch failed: %s", e)
    return []


async def cmd_investigate(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Deep investigation: Full token + wallet + holder + trace analysis.

    Usage: /investigate <address>
    Pro tier only.
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
            "🕵️ <b>Deep Investigation</b>\n\n"
            + usage_hint(
                "investigate",
                "address",
                "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
                extra=(
                    "Runs a comprehensive investigation combining:\n"
                    "  • Full security scan (honeypot, rug, bundle)\n"
                    "  • Wallet forensics (dev wallet, funding)\n"
                    "  • Holder concentration analysis\n"
                    "  • Cross-chain fund tracing\n"
                    "  • Smart money detection\n"
                    "  • AI-powered risk assessment\n\n"
                    "🛰️ <b>Monitor (250★/30d)</b> — live LP/whale + tax/ownership/mint alerts · 🔬 <b>Deep Scan (250★)</b> — full forensics"
                ),
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    tier = db.get_user_tier(user.id)
    if tier != "pro" and not is_owner(user.id):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "🕵️ <b>Deep Investigation</b>\n\n"
            "This feature requires <b>Pro tier</b>.\n\n"
            "🛰️ <b>Monitor (250★/30d)</b> — live LP/whale + tax/ownership/mint alerts\n"
            "🔬 <b>Deep Scan (250★)</b> — full forensics on demand\n\n"
            "Upgrade now to unlock full investigative power!",
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
        f"🕵️ <b>Deep Investigation Started</b>\n"
        f"<code>{address}</code>\n\n"
        f"⏳ Phase 1: Running security scan...\n"
        f"⏳ Phase 2: Analyzing wallet forensics...\n"
        f"⏳ Phase 3: Tracing fund flows...\n"
        f"⏳ Phase 4: Generating verdict...",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Phase 1-4: Run all analysis in parallel
        client = get_shared_client(45)
        # Scan + wallet + cross-chain run concurrently (previously
        # sequential — up to ~75s worst case). Dev-wallet forensics wait
        # on the scan result (dev_wallet comes from the scan).
        scan_result, wallet_data, cross_chain = await asyncio.gather(
            _run_scan(address, chain),
            _fetch_wallet_tokens(client, address),
            _fetch_cross_chain(client, address, chain),
            return_exceptions=True,
        )
        if isinstance(scan_result, BaseException):
            raise scan_result
        if not isinstance(wallet_data, dict):
            wallet_data = {}
        if not isinstance(cross_chain, list):
            cross_chain = []

        # Dev wallet forensics (if available) — waits on the scan result
        dev_data: dict[str, Any] = {}
        dev_wallet = scan_result.get("dev_wallet")
        if dev_wallet:
            try:
                dev_resp = await client.get(
                    f"{BACKEND_URL}/api/v1/databus/wallet/{dev_wallet}/tokens",
                    headers=backend_headers(),
                    timeout=10,
                )
                if dev_resp.status_code == 200:
                    dev_data = dev_resp.json()
            except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
                logger.warning("investigate token fetch failed: %s", e)

        # Build investigation report
        verdict, verdict_desc = _determine_verdict(scan_result, wallet_data)
        verdict_emoji = _VERDICT_EMOJIS.get(verdict, "⚪")

        s = sep()
        d = thin_sep()

        lines = [
            "🕵️ <b>Investigation Report</b>",
            s,
            f"<b>{scan_result['name']}</b> ({scan_result['symbol']})",
            f"<code>{address}</code>",
            f"⛓️ {scan_result['chain']}",
            "",
        ]

        # Verdict (top-line)
        lines.extend(
            [
                f"<b>🏆 Verdict: {verdict_emoji} {verdict}</b>",
                f"<i>{verdict_desc}</i>",
                s,
            ]
        )

        # Market Overview
        lines.extend(
            [
                "<b>📊 Market Overview</b>",
                d,
                f"💵 Price: <b>{fmt_number(scan_result['price'])}</b>",
                f"📊 Market Cap: <b>{fmt_number(scan_result['mcap'])}</b>",
                f"💧 Liquidity: <b>{fmt_number(scan_result['liquidity'])}</b>",
                f"📈 24h Vol: <b>{fmt_number(scan_result['volume_24h'])}</b>",
                f"🔄 1h: {fmt_pct(scan_result['price_change_1h'])} | 24h: {fmt_pct(scan_result['price_change_24h'])}",
                "",
            ]
        )

        # Security
        lines.extend(
            [
                "<b>🔒 Security Analysis</b>",
                d,
                risk_bar(scan_result["risk_score"]),
            ]
        )
        if scan_result["threats"]:
            for t in scan_result["threats"]:
                lines.append(f"  🔴 {t}")
        else:
            lines.append("  ✅ No threats detected")
        # v2 explainable scoring
        if scan_result.get("safety_score") is not None:
            safety = scan_result["safety_score"]
            emoji = "🟢" if safety >= 80 else "🟡" if safety >= 40 else "🔴"
            lines.append(
                f"  {emoji} Safety Score: <b>{safety:.0f}/100</b> | "
                f"Confidence: {scan_result.get('confidence', 0):.0f}% | "
                f"Risk: <b>{scan_result.get('risk_level', 'unknown').upper()}</b>"
            )
            if scan_result.get("breakdown"):
                from app.domains.telegram.rugmunchbot.formatters import format_scoring_breakdown

                lines.append(format_scoring_breakdown(scan_result["breakdown"]))
        lines.append("")

        # Smart Contract Checks
        checks = []
        if scan_result["honeypot"] is not None:
            checks.append(
                f"  {'🔴' if scan_result['honeypot'] else '✅'} Honeypot: {'YES' if scan_result['honeypot'] else 'No'}"
            )
        if scan_result["contract_verified"] is not None:
            checks.append(
                f"  {'✅' if scan_result['contract_verified'] else '🔴'} Source Verified: {'Yes' if scan_result['contract_verified'] else 'NO'}"
            )
        if scan_result["mintable"] is not None:
            checks.append(
                f"  {'🔴' if scan_result['mintable'] else '✅'} Mintable: {'YES' if scan_result['mintable'] else 'No'}"
            )
        if scan_result["owner_renounced"] is not None:
            checks.append(
                f"  {'✅' if scan_result['owner_renounced'] else '🔴'} Ownership: {'Renounced' if scan_result['owner_renounced'] else 'NOT RENOUNCED'}"
            )
        if scan_result["lp_locked"] is not None:
            checks.append(
                f"  {'🔴' if scan_result['lp_locked'] < 0.5 else '✅'} LP Locked: {scan_result['lp_locked'] * 100:.0f}%"
            )
        if checks:
            lines.extend(checks)
            lines.append("")

        # Holder Analysis
        top_holders = scan_result.get("top_holders", [])
        if top_holders:
            lines.extend(
                [
                    "<b>👥 Holder Analysis</b>",
                    d,
                ]
            )
            total_top10 = sum(h.get("pct", 0) for h in top_holders[:10])
            lines.append(f"  📊 Top 10 Concentration: <b>{total_top10:.1f}%</b>")
            for i, h in enumerate(top_holders[:5], 1):
                tag = f" [{h.get('tag', '')}]" if h.get("tag") else ""
                cf = " 📄" if h.get("is_contract") else ""
                bar_len = 8
                pct = h.get("pct", 0)
                filled = min(int((pct / 25) * bar_len), bar_len)
                bar = "🟥" * filled + "⬜" * (bar_len - filled)
                lines.append(f"  {i}. <code>{h['address']}</code> {bar} {pct:.1f}%{tag}{cf}")
            lines.append("")

        # Wallet Forensics
        if wallet_data:
            lines.extend(
                [
                    "<b>👛 Wallet Forensics</b>",
                    d,
                    f"  💰 Balance: <b>{fmt_number(wallet_data.get('balance_usd', 0))}</b>",
                    f"  📝 Transactions: <b>{wallet_data.get('tx_count', 0):,}</b>",
                    f"  ⏳ Age: <b>{wallet_data.get('wallet_age_days', 0)} days</b>",
                ]
            )
            pnl = wallet_data.get("pnl_estimate")
            if pnl is not None:
                lines.append(f"  {'📈' if pnl >= 0 else '📉'} Est. P&L: <b>{fmt_number(pnl)}</b>")

            funding = wallet_data.get("funding_source")
            if funding:
                lines.append(f"  💰 Source: {funding}")

            flags = wallet_data.get("flags", [])
            if flags:
                lines.append("")
                for flag in flags[:3]:
                    lines.append(f"  ⚠️ {flag}")
            lines.append("")

        # Dev Wallet Analysis
        if dev_data:
            lines.extend(
                [
                    "👨‍💻 <b>Dev Wallet Intel</b>",
                    d,
                    f"  👛 <code>{dev_wallet}</code>",
                    f"  💰 Balance: <b>{fmt_number(dev_data.get('balance_usd', 0))}</b>",
                    f"  📝 Txns: <b>{dev_data.get('tx_count', 0):,}</b>",
                    f"  ⏳ Age: <b>{dev_data.get('wallet_age_days', 0)} days</b>",
                ]
            )
            dev_pnl = dev_data.get("pnl_estimate")
            if dev_pnl is not None:
                lines.append(f"  {'📈' if dev_pnl >= 0 else '📉'} Dev P&L: <b>{fmt_number(dev_pnl)}</b>")
            lines.append("")

        # Cross-Chain
        if cross_chain:
            lines.extend(
                [
                    "🌉 <b>Cross-Chain Activity</b>",
                    d,
                ]
            )
            for conn in cross_chain[:4]:
                src = conn.get("source_chain", "")
                dst = conn.get("dest_chain", "")
                amt = conn.get("amount_usd", 0)
                lines.append(f"  🌐 {src} → {dst}" + (f" ${fmt_number(amt)}" if amt else ""))
            lines.append("")

        # Advanced threat detection
        adv_threats = []
        if scan_result.get("bundle_detected"):
            adv_threats.append("📦 Bundle activity detected — coordinated buys from funded wallets")
        if scan_result.get("fresh_wallet_pct", 0) > 50:
            adv_threats.append(f"🆕 Fresh wallets: {scan_result['fresh_wallet_pct']:.0f}% — possible fake holders")
        if scan_result.get("volume_real_ratio", 0) > 5:
            adv_threats.append(f"📊 Suspicious Vol/MCap ratio: {scan_result['volume_real_ratio']:.1f}x")
        if wallet_data.get("is_fresh"):
            adv_threats.append("🆕 Target wallet is fresh (<7 days old) — higher scam correlation")

        if adv_threats:
            lines.extend(
                [
                    "<b>🔍 Advanced Threat Detection</b>",
                    d,
                ]
            )
            for t in adv_threats:
                lines.append(f"  🚨 {t}")
            lines.append("")

        # Summary bar
        total_flags = len(scan_result.get("threats", [])) + len(wallet_data.get("flags", [])) + len(adv_threats)
        lines.extend(
            [
                s,
                "<b>Report Summary</b>",
                f"  🏆 Verdict: {verdict_emoji} <b>{verdict}</b>",
                f"  🚩 Flags Raised: <b>{total_flags}</b>",
                f"  📊 Risk Score: <b>{scan_result['risk_score']}%</b>",
                "  🕵️ Investigation Depth: <b>Full</b>",
            ]
        )
        lines.append(footer_links())

        db.increment_usage(user.id, scan=True)
        db.log_scan(user.id, address, chain, "investigate", scan_result.get("risk_score", 0))

        # Split into multiple messages if too long
        full_text = "\n".join(lines)
        if len(full_text) > 4000:
            # Send truncated investigation with full report link
            summary_lines = lines[: lines.index(s) + 1]  # Up to first sep
            summary_lines.extend(
                [
                    f"🏆 Verdict: {verdict_emoji} <b>{verdict}</b>",
                    f"🚩 Flags: {total_flags} | Risk: {scan_result['risk_score']}%",
                    f"📊 MCap: {fmt_number(scan_result['mcap'])} | Liq: {fmt_number(scan_result['liquidity'])}",
                    "",
                    "📋 <b>Full investigation too long for Telegram.</b>",
                    f"🌐 <a href='{WEBSITE_URL}/markets/token/{address}'>View full report on RugMunch.io</a>",
                ]
            )
            summary_lines.append(footer_links())
            await msg.edit_text(
                "\n".join(summary_lines),
                parse_mode=ParseMode.HTML,
                reply_markup=investigate_kb(address, chain),
                disable_web_page_preview=True,
            )
        else:
            await msg.edit_text(
                full_text,
                parse_mode=ParseMode.HTML,
                reply_markup=investigate_kb(address, chain),
                disable_web_page_preview=True,
            )

    except (httpx.HTTPError, ConnectionError, TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.debug("cmd_investigate_error", exc_info=True)
        logger.error(f"Investigation error: {e}")
        await msg.edit_text(
            failure_card(
                "Investigation Failed",
                f"Could not complete investigation for <code>{address}</code>.",
                ACTION_TRY_AGAIN,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
