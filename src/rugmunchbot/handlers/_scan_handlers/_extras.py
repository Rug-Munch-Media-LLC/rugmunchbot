#!/usr/bin/env python3
"""
RugMunchBot -- scan-family command handlers (cmd_quick + cmd_deployer + cmd_hype).

Split out of ``handlers/_scan_handlers.py`` (degodded 2026-08-16). Re-exported
by ``handlers._scan_handlers`` and ``handlers.scanning`` so existing
importers/tests keep working unchanged.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.config import (
    DEXSCREENER,
    fmt_chain,
    fmt_number,
    fmt_pct,
)
from app.domains.telegram.rugmunchbot.formatters import (
    detect_chain,
    footer_links,
    is_evm,
    is_owner,
    is_sol,
    paywall_text,
    sep,
    thin_sep,
    web_scan_button,
)
from app.domains.telegram.rugmunchbot.handlers._messages import (
    ACTION_TRY_AGAIN,
    failure_card,
)
from app.domains.telegram.rugmunchbot.handlers._scanning_helpers import (
    _bucket_emoji,
    _ds_pairs,
)
from app.domains.telegram.rugmunchbot.keyboards import (
    back_kb,
    upgrade_nudge_kb,
)
from app.domains.telegram.rugmunchbot.scanner import (
    fetch_deployer_fingerprint,
    fetch_hype_score,
    scan_token_v2,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import get_shared_client, http_get_json

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick price check - same as $TOKEN but as a command."""
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001fa99 Usage: /quick <code>SYMBOL</code>\nExample: /quick PEPE", parse_mode=ParseMode.HTML
        )
        return
    symbol = ctx.args[0].upper().lstrip("$")
    body = await http_get_json(f"{DEXSCREENER}/search/?q={symbol}", timeout=10)
    if body:
        pairs = _ds_pairs(body)
        if pairs:
            p = pairs[0]
            addr = p.get("baseToken", {}).get("address", "")
            chain = p.get("chainId", "")
            price = float(p.get("priceUsd", 0))
            h24 = p.get("priceChange", {}).get("h24") or 0
            vol = float(p.get("volume", {}).get("h24") or 0)
            mcap = float(p.get("marketCap") or 0)
            name = p.get("baseToken", {}).get("name", symbol)
            text = (
                f"\U0001fa99 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                f"<code>{addr}</code>\n\n"
                f"\U0001f4b0 {fmt_number(price)} | \U0001f504 24h: {fmt_pct(h24)}\n"
                f"\U0001f4c8 MCap: {fmt_number(mcap)} | \U0001f4a7 Vol: {fmt_number(vol)}"
                f"{footer_links()}"
            )
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{addr}_{chain}")],
                    [web_scan_button(addr, chain)],
                ]
            )
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return
    else:
        logger.warning(f"cmd_quick DexScreener lookup failed for symbol {symbol}")
    await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\u274c Could not find token: <b>{symbol}</b>\n\n"
        "Double-check the symbol, or paste the full address with /scan.",
        parse_mode=ParseMode.HTML,
    )


# ── /deployer — quick deployer fingerprint check ──


async def cmd_deployer(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick deployer fingerprint: run a v2 scan and surface dev reputation."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f468\u200d\U0001f4bb <b>Deployer Fingerprint</b>\n\n"
            "Usage: /deployer <code>address</code>\n\n"
            "Example: /deployer <code>0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045</code>\n\n"
            "Runs a v2 security scan and extracts the deployer reputation: "
            "dev risk score, deployment history, rug count.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=upgrade_nudge_kb(tier)
            )
            return
    target = ctx.args[0]
    chain = ctx.args[1] if len(ctx.args) > 1 else None
    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f468\u200d\U0001f4bb <b>Checking deployer fingerprint...</b>\n<code>{target}</code>",
        parse_mode=ParseMode.HTML,
    )
    try:
        data = await scan_token_v2(target, chain)
        if data is None:
            await msg.edit_text(
                failure_card("Deployer Check Failed", "The scanner is unavailable right now.", ACTION_TRY_AGAIN),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        evidence = data.get("evidence") or {}
        dev = evidence.get("deployer") or {}
        if not dev:
            await msg.edit_text(
                f"\u26aa <b>No Deployer Data</b>\n\n"
                f"<code>{target}</code>\n"
                f"No deployer reputation data returned by the scanner.\n\n"
                f"Safety: {data.get('safety_score', 'N/A')}/100 | "
                f"Risk level: {data.get('risk_level', 'unknown')}",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return
        risk_score = dev.get("risk_score")
        risk_label = dev.get("risk_label") or dev.get("reputation_label") or "UNKNOWN"
        dev_addr = dev.get("deployer_address") or dev.get("address") or "unknown"
        chain_norm = chain or detect_chain(target)

        # Enrich with the C7 deployer behavioral fingerprint (risk bucket,
        # success rate, launchpad) when the backend can compute it.
        fingerprint = await fetch_deployer_fingerprint(dev_addr, chain_norm)
        fp = fingerprint or {}

        risk_bucket = fp.get("risk_bucket") or risk_label
        total_deployments = fp.get("total_deployments")
        if total_deployments is None:
            total_deployments = dev.get("total_deployments") or dev.get("deployment_count")
        success_rate = fp.get("success_rate")
        launchpad = fp.get("launchpad")
        lines = [
            "\U0001f468\u200d\U0001f4bb <b>Deployer Fingerprint</b>",
            sep(),
            f"<code>{dev_addr}</code>\n",
            "\U0001f3af <b>Risk Profile</b>",
            thin_sep(),
        ]
        dev_risk = fp.get("risk_score")
        if dev_risk is None:
            dev_risk = risk_score
        if dev_risk is not None:
            emoji = "\U0001f7e2" if dev_risk < 30 else "\U0001f7e1" if dev_risk < 60 else "\U0001f534"
            lines.append(f"{emoji} Dev Risk Score: <b>{dev_risk}/100</b>")
        if risk_bucket:
            lines.append(f"{_bucket_emoji(str(risk_bucket))} Risk Bucket: <b>{risk_bucket}</b>")
        if total_deployments is not None:
            lines.append(f"\U0001f4e6 Total Deployments: <b>{total_deployments}</b>")
        if success_rate is not None:
            lines.append(f"\U0001f4c8 Success Rate: <b>{success_rate * 100:.0f}%</b>")
        if launchpad:
            lines.append(f"\U0001f680 Known Launchpad: <b>{launchpad}</b>")
        if dev.get("rug_count") is not None:
            lines.append(f"\U0001f4a5 Rug Count: <b>{dev['rug_count']}</b>")
        if fp.get("median_lifespan_hours") is not None:
            lines.append(f"\u231b Median Lifespan: <b>{fp['median_lifespan_hours']:.0f}h</b>")
        if evidence.get("holders"):
            h = evidence["holders"]
            lines.append(
                f"\U0001f465 Top-10 Holders: <b>{h.get('top_10_pct', 'N/A')}%</b> "
                f"| Holders: {h.get('holder_count', 'N/A')}"
            )
        if evidence.get("liquidity"):
            liq = evidence["liquidity"]
            locked = liq.get("locked_pct")
            if locked is not None:
                lines.append(f"\U0001f510 LP Locked: <b>{locked:.0f}%</b>")
        if evidence.get("security", {}).get("checks"):
            checks = evidence["security"]["checks"]
            fails = sum(1 for c in checks.values() if c.get("status") == "fail")
            lines.append(f"\U0001f512 Failed Security Checks: <b>{fails}</b>")
        lines.append("")
        lines.append(f"\U0001f50d Full scan: /scan <code>{target}</code>{footer_links()}")
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        logger.debug("cmd_deployer_error", exc_info=True)
        logger.error(f"cmd_deployer failed: {e}")
        await msg.edit_text(
            failure_card("Deployer Check Failed", "The deployer lookup hit an unexpected error.", ACTION_TRY_AGAIN),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )


# ── /hype — quick hype score ──


async def cmd_hype(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Quick hype score: boosts, volume, age, and price momentum."""
    user = update.effective_user
    if user is None:
        return
    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f525 <b>Hype Score</b>\n\n"
            "Usage: /hype <code>address_or_symbol</code>\n\n"
            "Example: /hype <code>PEPE</code>\n\n"
            "Returns a 0-100 hype score based on market activity, "
            "volume momentum, token age, and price action.",
            parse_mode=ParseMode.HTML,
        )
        return
    if not is_owner(user.id):
        allowed, _, _ = db.check_rate_limit(user.id, "scan")
        if not allowed:
            tier = db.get_user_tier(user.id)
            await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
                paywall_text(user.id, "scan"), parse_mode=ParseMode.HTML, reply_markup=upgrade_nudge_kb(tier)
            )
            return
    target = ctx.args[0]
    msg = await update.message.reply_text("\U0001f525 <b>Measuring hype...</b>", parse_mode=ParseMode.HTML)  # type: ignore[union-attr]  # PTB None-able fields
    try:
        # Resolve address: if it looks like a symbol, search DexScreener
        address = target
        chain = ""
        client = get_shared_client(15)
        if not (is_evm(target) or is_sol(target)):
            r = await client.get(f"{DEXSCREENER}/search/?q={target}")
            if r.status_code == 200:
                pairs = _ds_pairs(r.json())
                if pairs:
                    address = pairs[0].get("baseToken", {}).get("address", "")
                    chain = pairs[0].get("chainId", "")
        if not address:
            await msg.edit_text(
                f"\u274c Could not resolve token: <b>{target}</b>\n\n"
                "Check the symbol, or paste the full address with /scan.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Boosts (paid hype signal)
        boosts = 0
        try:
            br = await client.get(f"{DEXSCREENER}/token-boosts/top/v1")
            if br.status_code == 200:
                boost_list = br.json()
                if isinstance(boost_list, list):
                    for b in boost_list:
                        if b.get("tokenAddress", "").lower() == address.lower():
                            boosts = int(b.get("boostCount", 0) or 0)
                            break
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            pass

        # Pair data
        tr = await client.get(f"{DEXSCREENER}/tokens/{address}")
        if tr.status_code != 200:
            await msg.edit_text(
                "\u274c Token not found.\n\nCheck the address and try again.", parse_mode=ParseMode.HTML
            )
            return
        pairs = _ds_pairs(tr.json())
        if not pairs:
            await msg.edit_text(
                "\u274c No trading pairs found.\n\nEnsure the token has an active pair and try again.",
                parse_mode=ParseMode.HTML,
            )
            return
        p = pairs[0]
        name = p.get("baseToken", {}).get("name", "???")
        symbol = p.get("baseToken", {}).get("symbol", "???")
        chain = chain or p.get("chainId", "")
        price = float(p.get("priceUsd", 0))
        h1 = float(p.get("priceChange", {}).get("h1") or 0)
        h24 = float(p.get("priceChange", {}).get("h24") or 0)
        vol24 = float(p.get("volume", {}).get("h24") or 0)
        liq = float(p.get("liquidity", {}).get("usd") or 0)
        created = p.get("pairCreatedAt")
        from datetime import UTC, datetime as dt

        age_hours = 0.0
        if created:
            with contextlib.suppress(OSError, ValueError, TypeError):
                age_hours = max(0, (dt.now(UTC) - dt.fromtimestamp(created / 1000, tz=UTC)).total_seconds() / 3600)

        # ── C5 hype score from backend (preferred) ──
        # Composite 0-100 with per-signal breakdown: social velocity, volume
        # growth, holder growth, price momentum. Falls back to the local
        # DexScreener computation below when unavailable.
        c5 = await fetch_hype_score(address, chain)
        if c5 is not None and c5.get("hype_score") is not None:
            hype = float(c5["hype_score"])
            signals = c5.get("signals") or {}
            social = signals.get("social_velocity") or {}
            volume = signals.get("volume_growth") or {}
            holder = signals.get("holder_growth") or {}
            price_sig = signals.get("price_momentum") or {}
            emoji = "\U0001f525" if hype >= 70 else "\U0001f7e1" if hype >= 40 else "\U0001f7e2"
            lines = [
                f"{emoji} <b>Hype Score: {hype:.0f}/100</b>",
                sep(),
                f"<b>{name}</b> ({symbol}) {fmt_chain(chain)}",
                f"<code>{address}</code>\n",
                "\U0001f4ca <b>Components</b>",
                thin_sep(),
                f"\U0001f4e2 Social Velocity: <b>{float(social.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f4c8 Volume Growth: <b>{float(volume.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f465 Holder Growth: <b>{float(holder.get('score', 0) or 0):.0f}/100</b>",
                f"\U0001f680 Price Momentum: <b>{float(price_sig.get('score', 0) or 0):.0f}/100</b>",
            ]
            if c5.get("inactivity_hours") is not None:
                lines.append(f"\u23f3 Inactivity: {c5['inactivity_hours']:.0f}h (decay x{c5.get('decay_factor', 1.0)})")
            details = []
            for sig in (social, volume, holder, price_sig):
                if isinstance(sig, dict) and sig.get("detail"):
                    details.append(str(sig["detail"])[:60])
            if details:
                lines.append("")
                lines.append("\U0001f4cb <i>" + " | ".join(details[:4]) + "</i>")
            lines.append("")
            lines.append(f"\U0001f4b0 Price: {fmt_number(price)}")
            lines.append("")
            lines.append(f"\U0001f50d Full scan: /scan <code>{address}</code>{footer_links()}")
            kb = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}")],
                    [web_scan_button(address, chain)],
                ]
            )
            db.increment_usage(user.id, scan=True)
            await msg.edit_text(
                "\n".join(lines),
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return

        # ── Hype scoring (local fallback) ──
        boost_score = min(boosts * 8, 40)  # 0-40 from boost count
        vol_score = 0
        if vol24 > 0:
            if vol24 >= 1_000_000:
                vol_score = 20
            elif vol24 >= 100_000:
                vol_score = 14
            elif vol24 >= 10_000:
                vol_score = 8
            elif vol24 > 0:
                vol_score = 4
        momentum = 0
        if h1 > 20 and h24 > 30:
            momentum = 20
        elif h1 > 10 and h24 > 15:
            momentum = 14
        elif h24 > 5:
            momentum = 8
        elif h24 < -20:
            momentum = 0
        else:
            momentum = 4
        age_score = 0
        if age_hours and age_hours < 24:
            age_score = 10
        elif age_hours and age_hours < 72:
            age_score = 6
        elif age_hours and age_hours < 168:
            age_score = 3
        liquidity_score = 10 if liq >= 100_000 else 5 if liq >= 10_000 else 0
        hype = min(boost_score + vol_score + momentum + age_score + liquidity_score, 100)

        emoji = "\U0001f525" if hype >= 70 else "\U0001f7e1" if hype >= 40 else "\U0001f7e2"
        lines = [
            f"{emoji} <b>Hype Score: {hype}/100</b>",
            sep(),
            f"<b>{name}</b> ({symbol}) {fmt_chain(chain)}",
            f"<code>{address}</code>\n",
            "\U0001f4ca <b>Components</b>",
            thin_sep(),
            f"\U0001f525 Boosts: {boosts} (x8, max 40) -> {boost_score}",
            f"\U0001f4c8 Volume 24h: {fmt_number(vol24)} -> {vol_score}/20",
            f"\U0001f504 Momentum (1h {fmt_pct(h1)} / 24h {fmt_pct(h24)}): -> {momentum}/20",
            f"\u231b Age: {age_hours:.0f}h -> {age_score}/10",
            f"\U0001f4a7 Liquidity: {fmt_number(liq)} -> {liquidity_score}/10",
            "",
            f"\U0001f4b0 Price: {fmt_number(price)}",
        ]
        lines.append("")
        lines.append(f"\U0001f50d Full scan: /scan <code>{address}</code>{footer_links()}")
        kb = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("\U0001f50d Full Scan", callback_data=f"scan_{address}_{chain}")],
                [web_scan_button(address, chain)],
            ]
        )
        db.increment_usage(user.id, scan=True)
        await msg.edit_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            disable_web_page_preview=True,
        )
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as e:
        logger.debug("cmd_hype_error", exc_info=True)
        logger.error(f"cmd_hype failed: {e}")
        await msg.edit_text(
            failure_card("Hype Check Failed", "The hype score lookup hit an unexpected error.", ACTION_TRY_AGAIN),
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
