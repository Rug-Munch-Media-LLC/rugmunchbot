"""
Premium Bot Commands — RMI Backend capabilities exposed via Telegram.
All free during beta.

Commands:
  /mev       — MEV risk advisory (sandwich attacks, safe swap params)
  /impersonation — Token impersonation / fake-token audit
  /deathclock    — Token longevity prediction (rug/abandonment countdown)
  /launches      — New token launch feed with auto-risk flags
  /cluster       — Wallet clustering & funding path detection
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import BACKEND_URL, backend_headers
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_json, http_post_json

logger = get_logger("rmi.premium")

# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _backend_get(path: str, params: dict[str, Any] | None = None, timeout: float = 15.0) -> dict[str, Any] | None:
    """GET to RMI backend, return parsed JSON or None on any failure."""
    body = await http_get_json(
        f"{BACKEND_URL}{path}",
        headers=backend_headers(),
        params=params or {},
        timeout=timeout,
    )
    if body is None:
        logger.debug("backend GET %s failed", path)
    return body


async def _backend_post(path: str, json: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any] | None:
    """POST to RMI backend, return parsed JSON or None on any failure."""
    body = await http_post_json(
        f"{BACKEND_URL}{path}",
        headers=backend_headers(),
        json=json or {},
        timeout=timeout,
    )
    if body is None:
        logger.debug("backend POST %s failed", path)
    return body


def _fmt_pct(v: float | None, digits: int = 1) -> str:
    if v is None:
        return "N/A"
    return f"{v:.{digits}f}%"


def _fmt_usd(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:,.2f}"


# ═══════════════════════════════════════════════════════════════════════════
# /mev — MEV RISK ADVISORY
# ═══════════════════════════════════════════════════════════════════════════


async def cmd_mev(update, ctx) -> None:
    """Handle /mev — MEV risk check for a token."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🛡️ <b>MEV Risk Advisory</b>\n\n"
            "Usage: <code>/mev &lt;token_address&gt; [chain]</code>\n\n"
            "Checks sandwich attack risk and returns safe swap parameters:\n"
            "• Frontrunning risk score\n"
            "• Recommended slippage tolerance\n"
            "• Optimal gas settings\n"
            "• MEV-blocker routing suggestion\n\n"
            "Example: <code>/mev 0x...</code>\n"
            "<i>Free during beta — protects your trades from MEV bots.</i>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    chain = args[1] if len(args) > 1 else "ethereum"

    await msg.chat.send_action("typing")

    data = await _backend_get(f"/api/v1/mev-advisory/check/{address}", params={"chain": chain})

    if not data:
        await msg.reply_text(
            "❌ MEV advisory unavailable for this token.\nTry <code>/scan</code> for general security data.",
            parse_mode="HTML",
        )
        return

    risk = data.get("risk_level", "unknown")
    score = data.get("risk_score", 0)
    emoji = "🔴" if score >= 70 else "🟡" if score >= 40 else "🟢"

    lines = [
        "🛡️ <b>MEV Risk Advisory</b>",
        f"<code>{address}</code> ({chain.title()})",
        "",
        f"{emoji} <b>Risk:</b> {risk.title()} ({score}/100)",
    ]

    if data.get("sandwich_risk") is not None:
        lines.append(f"🥪 <b>Sandwich Attack Risk:</b> {_fmt_pct(data.get('sandwich_risk'))}")
    if data.get("frontrun_risk") is not None:
        lines.append(f"🏃 <b>Frontrun Risk:</b> {_fmt_pct(data.get('frontrun_risk'))}")

    # Safe swap params
    swap = data.get("safe_swap", {})
    if swap:
        lines.append("")
        lines.append("💱 <b>Safe Swap Params</b>")
        if swap.get("slippage_bps"):
            lines.append(f"  📏 Slippage: {swap['slippage_bps']} bps ({swap['slippage_bps'] / 100:.1f}%)")
        if swap.get("gas_price_gwei"):
            lines.append(f"  ⛽ Gas: {swap['gas_price_gwei']} gwei")
        if swap.get("use_mev_blocker"):
            lines.append("  🛡️ MEV-blocker routing: recommended")
        if swap.get("deadline_min"):
            lines.append(f"  ⏰ Deadline: {swap['deadline_min']} min")

    if data.get("notes"):
        lines.append("")
        lines.append(f"💡 {data['notes']}")

    lines.append("")
    lines.append("<i>Higher score = more MEV danger. Use recommended params to protect trades.</i>")

    await msg.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
# /impersonation — TOKEN IMPERSONATION AUDIT
# ═══════════════════════════════════════════════════════════════════════════


async def cmd_impersonation(update, ctx) -> None:
    """Handle /impersonation — detect fake tokens impersonating real ones."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🎭 <b>Impersonation Detector</b>\n\n"
            "Usage: <code>/impersonation &lt;token_address&gt; [chain]</code>\n\n"
            "Audits a token for impersonation red flags:\n"
            "• Name/symbol similarity to known tokens\n"
            "• Honeypot delegates & hidden permissions\n"
            "• Related malicious contracts\n"
            "• Fake website / social media checks\n\n"
            "Example: <code>/impersonation 0x...</code>\n"
            "<i>Free during beta — spot fake tokens before you buy.</i>",
            parse_mode="HTML",
        )
        return

    address = args[0]
    chain = args[1] if len(args) > 1 else "ethereum"

    await msg.chat.send_action("typing")

    data = await _backend_get(f"/api/v1/impersonation/audit/{address}", params={"chain": chain})

    if not data:
        await msg.reply_text(
            "❌ Impersonation audit unavailable.\nTry <code>/scan</code> for general security data.",
            parse_mode="HTML",
        )
        return

    risk = data.get("risk_level", "unknown")
    score = data.get("risk_score", 0)
    emoji = "🔴" if score >= 70 else "🟡" if score >= 40 else "🟢"

    lines = [
        "🎭 <b>Impersonation Audit</b>",
        f"<code>{address}</code> ({chain.title()})",
        "",
        f"{emoji} <b>Impersonation Risk:</b> {risk.title()} ({score}/100)",
    ]

    # Delegates
    delegates = data.get("delegates", [])
    if delegates:
        lines.append("")
        lines.append(f"🔑 <b>Suspicious Delegates ({len(delegates)})</b>")
        for d in delegates[:3]:
            addr = d.get("address", "")
            reason = d.get("reason", "unknown")
            lines.append(f"  • <code>{addr}</code> — {reason}")

    # Approvals
    approvals = data.get("approvals", [])
    if approvals:
        lines.append("")
        lines.append(f"📝 <b>Dangerous Approvals ({len(approvals)})</b>")
        for a in approvals[:3]:
            spender = a.get("spender", "")
            token = a.get("token", "")
            lines.append(f"  • {token} → <code>{spender}</code>")

    # Related contracts
    related = data.get("related_contracts", [])
    if related:
        lines.append("")
        lines.append(f"🔗 <b>Related Contracts ({len(related)})</b>")
        for c in related[:3]:
            addr = c.get("address", "")
            ctype = c.get("type", "unknown")
            lines.append(f"  • <code>{addr}</code> — {ctype}")

    # Verdict
    if data.get("verdict"):
        lines.append("")
        lines.append(f"⚖️ <b>Verdict:</b> {data['verdict']}")

    lines.append("")
    lines.append("<i>High score = likely impersonation. Verify contract on explorer before buying.</i>")

    await msg.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
# /deathclock — TOKEN LONGEVITY PREDICTION
# ═══════════════════════════════════════════════════════════════════════════


async def cmd_deathclock(update, ctx) -> None:
    """Handle /deathclock — predict token survival time."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "⏰ <b>Death Clock</b>\n\n"
            "Usage: <code>/deathclock &lt;chain&gt; &lt;token_address&gt;</code>\n\n"
            "Predicts how long until a token dies (rug pull or abandonment):\n"
            "• Days until likely death\n"
            "• Key risk factors (liquidity, holders, age)\n"
            "• Survival probability\n\n"
            "Example: <code>/deathclock ethereum 0x...</code>\n"
            "<i>Free during beta — know the risk before you hold.</i>",
            parse_mode="HTML",
        )
        return

    chain = args[0]
    address = args[1] if len(args) > 1 else args[0]

    await msg.chat.send_action("typing")

    data = await _backend_get(f"/api/v1/death-clock/predict/{chain}/{address}")

    if not data:
        await msg.reply_text(
            "❌ Death clock unavailable for this token.\nTry <code>/scan</code> for general security data.",
            parse_mode="HTML",
        )
        return

    days = data.get("days_remaining")
    risk = data.get("risk_level", "unknown")
    factors = data.get("factors", {})

    if days is None:
        days_str = "Unknown"
        emoji = "❓"
    elif days <= 7:
        days_str = f"{days:.0f} days"
        emoji = "🔴"
    elif days <= 30:
        days_str = f"{days:.0f} days"
        emoji = "🟡"
    elif days <= 90:
        days_str = f"{days:.0f} days"
        emoji = "🟢"
    else:
        days_str = f"{days:.0f} days ({days / 30:.1f} months)"
        emoji = "🟢"

    lines = [
        "⏰ <b>Death Clock</b>",
        f"<code>{address}</code> ({chain.title()})",
        "",
        f"{emoji} <b>Estimated Survival:</b> {days_str}",
        f"📊 <b>Risk Level:</b> {risk.title()}",
    ]

    if data.get("survival_probability") is not None:
        lines.append(f"🎯 <b>Survival Probability:</b> {_fmt_pct(data.get('survival_probability'))}")

    if factors:
        lines.append("")
        lines.append("📋 <b>Risk Factors</b>")
        for key, val in list(factors.items())[:5]:
            factor_name = key.replace("_", " ").title()
            bar = "🔴" if val >= 0.7 else "🟡" if val >= 0.4 else "🟢"
            lines.append(f"  {bar} {factor_name}: {val:.0%}")

    if data.get("explanation"):
        lines.append("")
        lines.append(f"💡 {data['explanation']}")

    lines.append("")
    lines.append("<i>Lower survival = higher rug/abandonment risk. DYOR before holding.</i>")

    await msg.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
# /launches — NEW TOKEN LAUNCH FEED
# ═══════════════════════════════════════════════════════════════════════════


async def cmd_launches(update, ctx) -> None:
    """Handle /launches — new token launch feed with risk flags."""
    msg = update.message
    args = ctx.args

    chain = args[0] if args else None
    params = {}
    if chain:
        params["chain"] = chain

    await msg.chat.send_action("typing")

    data = await _backend_get("/api/v1/launches/feed", params=params)

    if not data:
        await msg.reply_text(
            "❌ Launch feed unavailable right now.\nPlease try again in a moment.",
            parse_mode="HTML",
        )
        return

    launches = data.get("launches", [])
    if not launches:
        chain_str = f" on {chain.title()}" if chain else ""
        await msg.reply_text(
            f"🔭 <b>Launch Feed</b>\n\n"
            f"No new launches detected{chain_str} in the last 24h.\n\n"
            f"<i>Check back soon — new tokens launch every minute.</i>",
            parse_mode="HTML",
        )
        return

    chain_str = f" — {chain.title()}" if chain else ""
    lines = [
        f"🔭 <b>New Token Launches{chain_str}</b>",
        f"Showing {len(launches)} recent launches\n",
    ]

    for i, launch in enumerate(launches[:8], 1):
        name = launch.get("name", "Unknown")
        symbol = launch.get("symbol", "???")
        addr = launch.get("address", "")
        launch_chain = launch.get("chain", "?")
        risk = launch.get("risk_level", "unknown")
        age_h = launch.get("age_hours", 0)

        risk_emoji = "🔴" if risk == "high" else "🟡" if risk == "medium" else "🟢"
        age_str = f"{age_h:.1f}h ago" if age_h < 24 else f"{age_h / 24:.1f}d ago"

        lines.append(f"{i}. {risk_emoji} <b>{name}</b> ({symbol}) — {launch_chain}")
        lines.append(f"   <code>{addr}</code>")
        lines.append(f"   Risk: {risk.title()} | Age: {age_str}")

        if launch.get("liquidity_usd"):
            lines.append(f"   Liquidity: {_fmt_usd(launch.get('liquidity_usd'))}")
        if launch.get("holders"):
            lines.append(f"   Holders: {launch['holders']}")

        lines.append("")

    lines.append("<i>🟢 Low risk | 🟡 Medium | 🔴 High risk — auto-flagged by scanner.</i>")

    await msg.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


# ═══════════════════════════════════════════════════════════════════════════
# /cluster — WALLET CLUSTERING
# ═══════════════════════════════════════════════════════════════════════════


async def cmd_cluster(update, ctx) -> None:
    """Handle /cluster — detect wallet clusters and funding paths."""
    msg = update.message
    args = ctx.args

    if not args:
        await msg.reply_text(
            "🕸️ <b>Wallet Clustering</b>\n\n"
            "Usage: <code>/cluster &lt;wallet_address&gt;</code>\n\n"
            "Detects related wallets and funding sources:\n"
            "• Clusters wallets by behavior/funding patterns\n"
            "• Traces fund origin (CEX, mixer, other tokens)\n"
            "• Identifies deployer-funded wallets (bundle detection)\n\n"
            "Example: <code>/cluster 0x...</code>\n"
            "<i>Free during beta — uncover hidden wallet connections.</i>",
            parse_mode="HTML",
        )
        return

    address = args[0]

    await msg.chat.send_action("typing")

    data = await _backend_post("/api/v1/wallet-clusters/detect", json={"address": address})

    if not data:
        await msg.reply_text(
            "❌ Clustering unavailable for this wallet.\nTry <code>/wallet</code> for basic forensics.",
            parse_mode="HTML",
        )
        return

    clusters = data.get("clusters", [])
    funding = data.get("funding_path", {})

    lines = [
        "🕸️ <b>Wallet Clustering</b>",
        f"<code>{address}</code>",
        "",
    ]

    if clusters:
        lines.append(f"🔗 <b>Detected Clusters ({len(clusters)})</b>")
        for c in clusters[:5]:
            cid = c.get("cluster_id", "")
            size = c.get("size", 0)
            ctype = c.get("type", "unknown")
            confidence = c.get("confidence", 0)
            lines.append(f"  • Cluster {cid}: {size} wallets ({ctype}, {confidence:.0%} confidence)")
            wallets = c.get("wallets", [])
            for w in wallets[:3]:
                lines.append(f"    └ <code>{w}</code>")
    else:
        lines.append("✅ No suspicious clusters detected.")

    if funding:
        lines.append("")
        lines.append("💰 <b>Funding Path</b>")
        if funding.get("source"):
            lines.append(f"  Source: {funding['source']}")
        if funding.get("chain"):
            lines.append(f"  Chain: {funding['chain'].title()}")
        if funding.get("amount_usd"):
            lines.append(f"  Amount: {_fmt_usd(funding.get('amount_usd'))}")
        hops = funding.get("hops", [])
        if hops:
            lines.append(f"  Hops: {len(hops)}")
            for hop in hops[:3]:
                lines.append(f"    → {hop.get('description', hop.get('address', ''))}")

    lines.append("")
    lines.append("<i>Clusters reveal coordinated wallets — useful for spotting teams & bundles.</i>")

    await msg.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)
