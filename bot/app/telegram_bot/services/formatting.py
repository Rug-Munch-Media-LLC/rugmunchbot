#!/usr/bin/env python3
"""
V3 output formatting — professional Telegram HTML.

Emoji risk indicators (🟢🟡🟠🔴), monospace addresses, bold key findings,
and blockquoted scanner warnings. All user-controlled values are HTML-escaped.
"""

import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import (
    TELEGRAM_CHANNEL,
    TWITTER_URL,
    WEBSITE_URL,
    fmt_number,
    fmt_pct,
    risk_bar,
)

_TIER_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
    "safe": "🟢",
    "unknown": "⚪",
}

_SEVERITY_EMOJI = {
    "critical": "🆘",
    "severe": "🆘",
    "high": "🔴",
    "danger": "🔴",
    "rugpull": "🔴",
    "honeypot": "🔴",
    "medium": "🟠",
    "warning": "🟠",
    "caution": "🟠",
    "low": "🟡",
    "info": "🟡",
    "informational": "🟡",
}


def esc(value) -> str:
    """HTML-escape arbitrary user/API-provided text."""
    return html.escape(str(value), quote=False)


def sep(char: str = "━", length: int = 28) -> str:
    return char * length


def thin_sep(char: str = "─", length: int = 24) -> str:
    return char * length


def footer_links() -> str:
    return (
        f"\n{thin_sep('·', 28)}\n"
        f'🌐 <a href="{WEBSITE_URL}">rugmunch.io</a> | '
        f'<a href="{TELEGRAM_CHANNEL}">📢 Alerts</a> | '
        f'<a href="{TWITTER_URL}">X</a>'
    )


def code(value) -> str:
    """Monospace wrapper for addresses/hashes."""
    return f"<code>{esc(value)}</code>"


def blockquote(text: str) -> str:
    """Blockquote wrapper for scanner warnings."""
    return f"<blockquote>{esc(text)}</blockquote>"


def risk_tier(score) -> str:
    """Map a 0-100 score to a tier name."""
    try:
        score = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 40:
        return "medium"
    if score >= 20:
        return "low"
    return "safe"


def risk_color_emoji(tier: str) -> str:
    return _TIER_EMOJI.get(str(tier).lower(), "⚪")


def severity_emoji(severity: str) -> str:
    return _SEVERITY_EMOJI.get(str(severity).lower(), "🟢")


def _market_line(data: dict) -> list[str]:
    """Market section — handles both v2 nested and legacy flat shapes."""
    market = data.get("market") or {}
    liq = data.get("liquidity") or {}
    price = market.get("price_usd") or data.get("price") or 0
    mcap = market.get("mcap_usd") or data.get("mcap") or 0
    if isinstance(liq, dict):
        liq_usd = liq.get("usd") or 0
        locked = liq.get("locked_pct")
    else:  # legacy flat shape: liquidity is a plain USD number
        liq_usd = liq or 0
        locked = None
    vol = market.get("volume_24h") or data.get("volume_24h") or 0

    lines = ["📊 <b>Market Data</b>", thin_sep()]
    lines.append(f"💰 Price: <b>{fmt_number(price)}</b>")
    lines.append(f"📈 Market Cap: <b>{fmt_number(mcap)}</b>")
    lines.append(f"💧 Liquidity: <b>{fmt_number(liq_usd)}</b>")
    if locked is not None:
        lines.append(f"🔒 LP Locked: <b>{float(locked) * 100:.0f}%</b>")
    lines.append(f"📊 24h Volume: <b>{fmt_number(vol)}</b>")
    return lines


def _warnings_block(warnings: list, tier: str) -> list[str]:
    """Scanner warnings — blockquoted, emoji-prefixed."""
    if not warnings:
        return ["✅ No major warnings"]
    emoji = "🔴" if tier in ("high", "critical") else "⚠️"
    return [blockquote(f"{emoji} {esc(w)}") for w in warnings[:6]]


def _age_str(data: dict) -> str:
    """Calculate human-readable token age."""
    created = data.get("created_at") or data.get("pair_created_at")
    if not created:
        return ""
    try:
        from datetime import UTC, datetime

        if isinstance(created, (int, float)):
            created_dt = datetime.fromtimestamp(
                created / 1000 if created > 1e10 else created, tz=UTC
            )
        else:
            created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        age = datetime.now(UTC) - created_dt
        if age.days > 365:
            return f"{age.days // 365}y {age.days % 365 // 30}mo"
        elif age.days > 0:
            return f"{age.days}d"
        else:
            return f"{age.seconds // 3600}h"
    except Exception:
        return ""


def _safety_score_bar(score: float) -> str:
    """Visual safety score bar: ████████░░ 78/100"""
    bars = 10
    filled = max(0, min(bars, int((score / 100) * bars)))
    block = "█" * filled + "░" * (bars - filled)
    tier_color = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "safe": "🟢"}
    rt = risk_tier(score)
    emoji = tier_color.get(rt, "🟢")
    return f"{emoji} <b>SAFETY SCORE:</b> {score:.0f}/100\n<code>{block}</code>"


def _float_or(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pct_str(v) -> str:
    """Format a potentially-None number as percentage string."""
    try:
        return f"{float(v):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _tax_str(v) -> str:
    """Format buy/sell tax — account for 0-1 vs 0-100 scaling."""
    try:
        vf = float(v)
        if vf <= 1.0:
            return f"{vf * 100:.0f}%"
        return f"{vf:.0f}%"
    except (TypeError, ValueError):
        return "N/A"


# ── Telegram-native safety grade emoji ──
def _safety_grade_emoji(score: float) -> str:
    if score >= 80:
        return "🟢 A"
    elif score >= 65:
        return "🟡 B"
    elif score >= 50:
        return "🟠 C"
    elif score >= 35:
        return "🟠 D"
    elif score >= 20:
        return "🔴 F"
    return "💀 F-"


def _holder_breakdown_emoji(data: dict, holder_count: int) -> str:
    """Build a one-line holder distribution if data is available."""
    holders = data.get("holders") or {}
    if isinstance(holders, dict) and holders:
        whales = holders.get("whales") or holders.get("whale_count") or 0
        sharks = holders.get("sharks") or holders.get("shark_count") or 0
        if whales or sharks:
            parts = []
            if whales:
                parts.append(f"🐋 {int(whales)} whales")
            if sharks:
                parts.append(f"🦈 {int(sharks)} sharks")
            if parts:
                return "  │  ".join(parts)
    return ""


def _deployer_digest(data: dict) -> str:
    """Render deployer summary line: tokens launched, rug rate, prior rugs."""
    deployer = data.get("deployer") or data.get("dev_reputation") or {}
    dev_wallet = deployer.get("dev_wallet") or deployer.get("creator") or deployer.get("address")
    if not dev_wallet:
        dev_wallet = data.get("dev_wallet") or ""
    total = (
        deployer.get("total_launched")
        or deployer.get("token_count")
        or deployer.get("total_deployments")
    )
    rugs = deployer.get("total_rugged") or deployer.get("rug_count")
    rep = deployer.get("reputation") or deployer.get("risk_level")
    if dev_wallet:
        lines = [f"🔍 Deployer: <code>{_short_addr(str(dev_wallet))}</code>"]
        if total:
            lines.append(f"📦 {int(total)} tokens launched")
            if rugs is not None:
                r = int(rugs)
                dead = int(total) - r
                lines.append(
                    f"{'💀 ' + str(r) + ' dead' if r > 0 else '✅ All active'} {'⚠️ ' + str(r) + ' prior rugs detected!' if r > 0 and rep else ''}"
                )
        elif rep:
            lines.append(f"⚠️ Reputation: {esc(str(rep)).upper()}")
        return "\n".join(lines)
    return ""


def _fresh_wallet_warning(data: dict) -> str:
    """Warning line for high fresh wallet percentage."""
    fwp = _float_or(data.get("fresh_wallet_pct") or 0)
    if fwp > 50:
        return f"⚠️ {fwp:.0f}% fresh wallets — possible fake holders"
    return ""


def _volume_warning(data: dict) -> str:
    """Warning for suspicious volume/mcap ratio."""
    ratio = data.get("volume_real_ratio")
    if ratio is not None and ratio > 3:
        return f"⚠️ Vol/MCap ratio: {ratio:.1f}x — possible wash trading"
    return ""


def format_v3_scan_report(r: dict) -> str:
    """Telegram-native beautiful scan report — bold headers, code addresses,
    emoji tier indicators, horizontal dividers, and information-dense layout."""
    name = r.get("name") or "Token"
    symbol = r.get("symbol") or r.get("ticker") or "???"
    chain = r.get("chain") or "unknown"
    risk = _float_or(r.get("risk_score") or 0)

    # ── Market data ──
    market = r.get("market") or {}
    price = _float_or(market.get("price_usd") or r.get("price") or 0)
    mcap = _float_or(market.get("mcap_usd") or r.get("mcap") or 0)
    liq_data = r.get("liquidity") or {}
    if isinstance(liq_data, dict):
        liq_usd = _float_or(liq_data.get("usd") or 0)
        locked_pct = liq_data.get("locked_pct")
    else:
        liq_usd = _float_or(liq_data or 0)
        locked_pct = None
    pc_1h = _float_or(market.get("price_change_1h") or r.get("price_change_1h") or 0)
    pc_24h = _float_or(market.get("price_change_24h") or r.get("price_change_24h") or 0)
    vol_24h = _float_or(market.get("volume_24h") or r.get("volume_24h") or 0)

    # ── Contract data ──
    contract = r.get("contract") or {}
    verified = contract.get("verified") or r.get("contract_verified")
    buy_tax = contract.get("buy_tax_pct") or r.get("buy_tax")
    sell_tax = contract.get("sell_tax_pct") or r.get("sell_tax")
    lp_locked = contract.get("lp_locked") or locked_pct or r.get("lp_locked")

    # ── Holder data ──
    holders = r.get("holders") or {}
    holder_count = holders.get("count") or holders.get("total") or r.get("holders") or 0
    if isinstance(holder_count, list):
        holder_count = len(holder_count)
    holder_count = int(holder_count) if holder_count else 0
    top10 = _float_or(holders.get("top10_pct") or r.get("top10_concentration") or 0)

    # ── Age ──
    age = _age_str(r)

    # ── Build report ──
    HR = "━" * 25
    address = r.get("address") or ""
    chain_emojis = {
        "solana": "◎",
        "ethereum": "⟠",
        "bsc": "🔶",
        "base": "🔷",
        "polygon": "🟣",
        "arbitrum": "🔵",
        "avalanche": "🔺",
    }
    chain_emoji = chain_emojis.get(str(chain).lower(), "⛓️")

    grade = _safety_grade_emoji(risk)
    lines = [
        f"🛡️ <b>{esc(name)}</b> (${esc(symbol)})",
        f"<code>{_short_addr(address)}</code> · {chain_emoji} {esc(str(chain)).capitalize()}",
        HR,
        f"💰 {fmt_number(price):>12} │ 📊 MCap {fmt_number(mcap)}",
    ]

    # Price changes with direction
    h1_dir = "📈" if pc_1h > 0 else "📉" if pc_1h < 0 else "➖"
    h24_dir = "📈" if pc_24h > 0 else "📉" if pc_24h < 0 else "➖"
    lines.append(
        f"{h1_dir} {esc(fmt_pct(pc_1h)):>10} │ {h24_dir} {esc(fmt_pct(pc_24h)):>10} │ 💧 Liq {fmt_number(liq_usd)}"
    )
    if vol_24h > 0:
        lines.append(f"🔄 Vol 24h: {fmt_number(vol_24h)}")

    # Safety score bar
    lines.extend(["", HR])
    bar_filled = max(1, min(10, int((risk / 100) * 10)))
    bar = "█" * bar_filled + "░" * (10 - bar_filled)
    lines.append(f"{grade} SAFETY: {risk:.0f}/100")
    lines.append(f"<code>{bar}</code>")

    # Security checks row
    lines.extend(["", HR])

    # Verified + LP Lock
    def _verified_str(v):
        if v is True or v == "yes":
            return "✅ Verified"
        elif v is False or v == "no":
            return "🔴 Unverified"
        return "⚪ Verif N/A"

    def _tax_str_v2(tax_val, which):
        try:
            v = float(tax_val)
            if v <= 1.0:
                v *= 100
            if v > 5:
                return f"🔴 {which} {v:.0f}%"
            elif v > 0.1:
                return f"⚠️ {which} {v:.0f}%"
            else:
                return f"✅ {which} {v:.0f}%"
        except Exception:
            return f"⚪ {which} N/A"

    def _lp_locked_str_v2(lp_val):
        try:
            v = float(lp_val)
            if v <= 1.0:
                v *= 100
            if v >= 80:
                return f"✅ LP Locked {v:.0f}%"
            elif v > 0:
                return f"⚠️ LP Locked {v:.0f}%"
            else:
                return "🔴 LP Not Locked"
        except Exception:
            return "⚪ LP Lock N/A"

    security_row = f"{_verified_str(verified)}  │  {_lp_locked_str_v2(lp_locked)}"
    lines.append(security_row)

    tax_row = f"{_tax_str_v2(buy_tax, 'Buy')}  │  {_tax_str_v2(sell_tax, 'Sell')}"
    lines.append(tax_row)

    # Holder + age row
    lines.extend(
        [
            "",
            HR,
            f"👥 {holder_count:,} holders  │  🐋 Top 10: {_pct_str(top10)}",
            f"📅 {age or 'N/A'} old  │  ⛓️ {esc(str(chain)).upper()}",
        ]
    )

    # Holder breakdown (whales/sharks/fish/shrimp)
    hodl_breakdown = _holder_breakdown_emoji(r, holder_count)
    if hodl_breakdown:
        lines.append(hodl_breakdown)

    # Deployer digest
    deployer_digest = _deployer_digest(r)
    if deployer_digest:
        lines.extend(["", HR, deployer_digest])

    # Fresh wallet warning
    fw_warn = _fresh_wallet_warning(r)
    if fw_warn:
        lines.extend(["", HR, fw_warn])

    # Volume warning
    vol_warn = _volume_warning(r)
    if vol_warn:
        lines.append(vol_warn)

    # Threats/warnings as blockquote
    warnings = r.get("warnings") or r.get("factors") or r.get("threats") or []
    if warnings:
        lines.extend(["", HR])
        for w in warnings[:5]:
            lines.append(f"> 🔴 {esc(str(w)[:100])}")

    # Timestamp
    from datetime import UTC, datetime as dt

    ts = dt.now(UTC).strftime("%H:%M UTC")
    lines.extend(["", HR, f"<i>Scanned {ts} · Live data</i>"])

    lines.append(footer_links())
    return "\n".join(lines)


def format_quick_risk(r: dict) -> str:
    """Lightweight risk-only report."""
    risk = r.get("risk_score") or 0
    tier = r.get("risk_tier") or risk_tier(risk)
    address = r.get("address") or r.get("token_address") or ""
    warnings = r.get("warnings") or r.get("factors") or []

    lines = ["⚡ <b>Quick Risk Assessment</b>", sep()]
    if address:
        lines.append(code(address))
    lines.extend(
        [
            "",
            f"{risk_color_emoji(tier)} Risk Score: <b>{risk:.0f}%</b> (<b>{esc(tier).upper()}</b>)",
            risk_bar(risk),
        ]
    )
    if warnings:
        lines.append("")
        lines.extend(_warnings_block(warnings[:3], tier))
    lines.append("")
    lines.append("💡 Run /scan for the full report.")
    return "\n".join(lines)


def format_deepscan_report(r: dict) -> str:
    """Telegram-native deep forensic report — multi-section with Rug Muncher commentary."""
    address = r.get("address") or r.get("token_address") or ""
    score = r.get("risk_score") or r.get("score") or 0
    summary = r.get("summary") or r.get("verdict") or r.get("result") or ""
    findings = r.get("findings") or r.get("warnings") or r.get("flags") or []

    # ── Deep analysis metrics ──
    bundle = r.get("bundle") or {}
    bundle_count = bundle.get("wallet_count") or r.get("bundle_wallets") or 0
    bundle_pct = bundle.get("supply_pct") or r.get("bundle_supply_pct") or 0
    sniper_count = r.get("sniper_count") or r.get("sniper_activity") or 0
    dev_links = r.get("dev_connections") or r.get("linked_wallets") or 0
    if isinstance(dev_links, list):
        dev_links = len(dev_links)
    rug_prob = r.get("rug_probability") or r.get("rug_score") or 0

    # ── Holder breakdown ──
    holder_data = r.get("holders") or {}
    whales = holder_data.get("whales") or holder_data.get("whale_count") or 0
    whales_pct = holder_data.get("whales_pct") or holder_data.get("whale_pct") or 0
    sharks = holder_data.get("sharks") or holder_data.get("shark_count") or 0
    sharks_pct = holder_data.get("sharks_pct") or holder_data.get("shark_pct") or 0
    fish = holder_data.get("fish") or holder_data.get("fish_count") or 0
    fish_pct = holder_data.get("fish_pct") or 0
    shrimp = holder_data.get("shrimp") or holder_data.get("shrimp_count") or 0
    shrimp_pct = holder_data.get("shrimp_pct") or 0

    # ── RAG insight ──
    rag = r.get("rag_insight") or r.get("ai_insight") or ""

    HR = "━" * 25
    grade = _safety_grade_emoji(score)

    lines = [
        f"🔬 <b>DEEP ANALYSIS</b> — {grade}",
        HR,
    ]
    if address:
        lines.append(f"<code>{_short_addr(str(address))}</code>")

    # Deep metrics row
    lines.extend(["", f"📦 Bundle: {int(bundle_count)} wallets ({_pct_str(bundle_pct)})"])
    lines.append(f"🕵️ Snipers: {int(sniper_count)} detected")
    lines.append(f"🔗 Dev Links: {int(dev_links)} connected")
    lines.append(f"💀 Rug Prob: {_pct_str(rug_prob)}")

    if summary:
        lines.extend(["", f"> {esc(str(summary)[:400])}"])

    # ── Holder breakdown ──
    lines.extend(["", HR, "📊 <b>HOLDERS</b>"])
    if any([whales, sharks, fish, shrimp]):
        lines.append(
            f"🐋 Whales: {int(whales)} ({_pct_str(whales_pct)})  │  🦈 Sharks: {int(sharks)} ({_pct_str(sharks_pct)})"
        )
        lines.append(
            f"🐟 Fish: {int(fish)} ({_pct_str(fish_pct)})  │  🐠 Shrimp: {int(shrimp)} ({_pct_str(shrimp_pct)})"
        )
    else:
        lines.append("⚠️ Detailed holder breakdown not available")

    # ── Rug Muncher commentary ──
    lines.extend(["", HR, "🤖 <b>Rug Muncher says:</b>"])
    if rag:
        lines.append(f"> {esc(str(rag)[:600])}")
    elif rug_prob >= 60:
        lines.append(
            "> This deployer has launched multiple tokens with a high rug rate. "
            "The concentration metrics suggest pre-rug positioning. Caveat emptor."
        )
    elif score >= 60:
        lines.append(
            "> Multiple red flags detected. High risk — proceed with extreme caution "
            "if you value your capital."
        )
    elif score >= 30:
        lines.append(
            "> Some concerning signals but nothing definitively malicious. "
            "Standard DeFi risks apply — don't bet the house."
        )
    else:
        lines.append(
            "> This token passes the basic sniff test. No major red flags detected. "
            "But in crypto, nothing is truly safe. Stay vigilant."
        )

    # ── Findings ──
    if findings:
        lines.extend(["", HR])
        for item in findings[:8]:
            if isinstance(item, dict):
                sev = item.get("severity") or item.get("level") or "info"
                label = item.get("title") or item.get("finding") or item.get("label") or str(item)
                lines.append(f"  {severity_emoji(str(sev))} {esc(label)}")
            else:
                lines.append(f"  • {esc(str(item)[:200])}")

    # Pass-through sections
    for key in ("liquidity", "deployer", "market", "contract", "timeline"):
        section = r.get(key)
        if isinstance(section, dict) and section:
            lines.extend(["", f"🔎 <b>{key.title()}</b>"])
            for k, v in section.items():
                if v is not None:
                    label = str(k).replace("_", " ").title()
                    lines.append(f"  {esc(label)}: <b>{esc(str(v)[:100])}</b>")

    lines.append(footer_links())
    return "\n".join(lines)


def format_ai_verdict(address: str, verdict: str, scan_ctx: dict | None = None) -> str:
    """AI analysis report — scanner context bar + blockquoted verdict."""
    lines = ["🤖 <b>RugMunch AI Analysis</b>", sep()]
    if address:
        lines.append(code(address))
    if scan_ctx:
        risk = scan_ctx.get("risk_score") or 0
        if risk:
            lines.extend(["", f"📊 Scanner context — Risk: <b>{risk:.0f}%</b>", risk_bar(risk)])
    lines.append("")
    lines.append(blockquote(verdict))
    lines.extend(
        [
            "",
            "💡 AI analysis is informational — always DYOR and verify with /scan.",
            footer_links(),
        ]
    )
    return "\n".join(lines)


def format_market_rundown(text: str) -> str:
    """Market rundown report."""
    return f"📊 <b>Market Rundown</b>\n{sep()}\n\n{blockquote(text)}\n{footer_links()}"


def format_advanced_scan_report(r: dict) -> str:
    """Full paid advanced scan — all sections including tx history, social sentiment,
    similar token comparisons, and risk factor checklist."""
    address = r.get("address") or r.get("token_address") or ""
    score = r.get("risk_score") or r.get("score") or 0

    # ── Transaction summary ──
    tx_data = r.get("transactions") or r.get("tx_summary") or {}
    tx_buys = tx_data.get("buys_24h") or tx_data.get("total_buys") or 0
    tx_sells = tx_data.get("sells_24h") or tx_data.get("total_sells") or 0
    tx_volume = tx_data.get("volume_24h") or tx_data.get("total_volume") or 0
    tx_unique = tx_data.get("unique_traders") or tx_data.get("unique_wallets") or 0

    # ── Social sentiment ──
    social = r.get("social") or r.get("sentiment") or {}
    social_score = social.get("score") or social.get("sentiment_score") or 0
    social_mentions = social.get("mentions") or social.get("twitter_mentions") or 0
    social_trending = social.get("trending") or social.get("is_trending") or False

    # ── Similar tokens ──
    similar = r.get("similar_tokens") or r.get("comparable") or []

    # ── Risk checklist ──
    checklist = r.get("risk_checklist") or r.get("checklist") or _build_checklist(r)

    lines = [
        "🛡️ <b>ADVANCED SCAN REPORT</b>",
        sep(),
        f"<code>{esc(address[:42])}</code>" if address else "",
        "",
        _safety_score_bar(score),
        "",
    ]

    # Transaction history
    lines.extend(
        [
            "📊 <b>TRANSACTION HISTORY (24h)</b>",
            sep(),
            f"✅ Buys: <b>{int(tx_buys):,}</b>  │  ❌ Sells: <b>{int(tx_sells):,}</b>",
            f"💰 Volume: <b>{fmt_number(tx_volume)}</b>  │  👥 Traders: <b>{int(tx_unique):,}</b>",
            f"📈 Buy/Sell Ratio: <b>{tx_buys / max(tx_sells, 1):.1f}x</b>"
            if tx_sells > 0
            else "📈 Buy/Sell Ratio: <b>∞</b>",
        ]
    )

    # Social sentiment
    sent_emoji = "📈" if social_score > 0 else "📉" if social_score < 0 else "-"
    trend_str = "🔥 Trending" if social_trending else ""
    lines.extend(
        [
            "",
            "📣 <b>SOCIAL SENTIMENT</b>",
            sep(),
            f"{sent_emoji} Sentiment: <b>{_pct_str(social_score)}</b> {trend_str}",
            f"🐦 Mentions: <b>{int(social_mentions):,}</b>",
        ]
    )

    # Similar tokens
    if similar:
        lines.extend(["", "🔄 <b>SIMILAR TOKENS</b>", sep()])
        for i, tok in enumerate(similar[:5], 1):
            if isinstance(tok, dict):
                tname = tok.get("name", "") or tok.get("symbol", "???")
                outcome = tok.get("outcome") or tok.get("result") or "unknown"
                sim = tok.get("similarity") or tok.get("score") or 0
                lines.append(
                    f"  {i}. <b>{esc(tname[:20])}</b> — {esc(outcome)} ({_pct_str(sim)} match)"
                )
            else:
                lines.append(f"  {i}. {esc(str(tok)[:60])}")

    # Risk factor checklist
    if checklist:
        lines.extend(["", "⚠️ <b>RISK FACTOR CHECKLIST</b>", sep()])
        for item in checklist[:9]:
            if isinstance(item, dict):
                status = item.get("status") or item.get("result") or "unknown"
                label = item.get("label") or item.get("name") or str(item)
                icon = (
                    "✅"
                    if status in ("pass", "safe", "ok", "good", True)
                    else "⚠️"
                    if status in ("warn", "caution", "medium")
                    else "❌"
                )
                lines.append(f"  {icon} {esc(label)}")
            else:
                lines.append(f"  • {esc(str(item)[:80])}")

    lines.extend(["", footer_links()])
    return "\n".join(lines)


def _build_checklist(r: dict) -> list:
    """Build a risk factor checklist from scanner data."""
    items = []
    contract = r.get("contract") or {}

    items.append(
        {
            "label": "Contract Verified",
            "status": "pass"
            if (contract.get("verified") or r.get("contract_verified"))
            else "fail",
        }
    )
    items.append(
        {
            "label": "LP Locked",
            "status": "pass"
            if (_float_or(contract.get("lp_locked") or r.get("lp_locked"), 1) > 0.5)
            else "fail",
        }
    )
    items.append(
        {
            "label": "Honeypot Check",
            "status": "pass" if not (contract.get("is_honeypot") or r.get("honeypot")) else "fail",
        }
    )
    items.append(
        {
            "label": "Buy Tax < 10%",
            "status": "pass"
            if _float_or(contract.get("buy_tax_pct") or r.get("buy_tax"), 0) < 0.1
            else "warn",
        }
    )
    items.append(
        {
            "label": "Sell Tax < 10%",
            "status": "pass"
            if _float_or(contract.get("sell_tax_pct") or r.get("sell_tax"), 0) < 0.1
            else "warn",
        }
    )
    items.append(
        {
            "label": "Ownership Renounced",
            "status": "pass"
            if (contract.get("owner_renounced") or r.get("owner_renounced"))
            else "warn",
        }
    )
    items.append(
        {
            "label": "Not Mintable",
            "status": "pass" if not (contract.get("mintable") or r.get("mintable")) else "fail",
        }
    )
    items.append(
        {
            "label": "Fresh Wallet % < 50%",
            "status": "pass" if _float_or(r.get("fresh_wallet_pct"), 0) < 50 else "warn",
        }
    )
    items.append(
        {
            "label": "No Bundle Detection",
            "status": "pass" if not r.get("bundle_detected") else "fail",
        }
    )
    return items


def format_comprehensive_deepscan(data: dict) -> list[str]:
    """Comprehensive deep scan report — scores, deployer, bundles, clusters, sentinel.

    Returns list of message chunks (each <= 4096 chars for Telegram).
    Aggregates scoring API, deployer profile, wallet labels, holder bundles,
    token graph clusters, and sentinel findings into a single beautiful report.
    """
    address = data.get("address", "")
    chain = data.get("chain", "unknown")
    scoring = data.get("scoring") or {}
    deployer = data.get("deployer") or {}
    wallet_labels = data.get("wallet_labels") or {}
    holders = data.get("holders") or {}
    cluster_count = data.get("cluster_count", 0)
    suspicious_clusters = data.get("suspicious_clusters", 0)
    cluster_details = data.get("cluster_details") or []
    bundle_detected = data.get("bundle_detected", False)
    bundle_wallets = data.get("bundle_wallets", 0)
    bundle_pct = data.get("bundle_pct", 0.0)
    sentinel_findings = data.get("sentinel_findings") or []
    dev_reputation = data.get("dev_reputation") or {}

    overall_score = scoring.get("risk_score") or scoring.get("score") or data.get("risk_score") or 0
    grade = scoring.get("grade") or ""
    categories = scoring.get("categories") or scoring.get("breakdown") or {}
    flags = scoring.get("flags") or scoring.get("risk_flags") or []

    s = sep()
    t = thin_sep()

    # ── Chunk 1: Header + Safety Score + Category Breakdown ──
    lines: list[str] = [
        "🧠 <b>DEEP FORENSIC SCAN — FULL REPORT</b>",
        s,
    ]
    if address:
        lines.append(
            f"<code>{code(address[:42]).replace('<code>', '').replace('</code>', '')}</code>"
            if False
            else code(address[:42])
        )
    lines.append(f"⛓️ Chain: <b>{esc(chain).upper()}</b>")
    lines.append("")

    # Overall score with grade bar
    tier = risk_tier(overall_score)
    emoji = risk_color_emoji(tier)
    if grade:
        lines.append(
            f"{emoji} <b>SAFETY RATING:</b> {overall_score:.0f}/100 — <b>Grade {esc(str(grade))}</b>"
        )
    else:
        lines.append(f"{emoji} <b>SAFETY SCORE:</b> {overall_score:.0f}/100 ({esc(tier).upper()})")

    # Visual score bar
    bar_filled = max(1, min(10, int((overall_score / 100) * 10)))
    score_bar_viz = "█" * bar_filled + "░" * (10 - bar_filled)
    lines.append(f"<code>{score_bar_viz}</code>")
    lines.append("")

    # Category breakdown
    if categories:
        lines.append("📊 <b>CATEGORY BREAKDOWN</b>")
        lines.append(t)
        for cat_name, cat_score in sorted(
            categories.items(),
            key=lambda x: float(x[1]) if isinstance(x[1], (int, float)) else 0,
            reverse=True,
        ):
            try:
                s_val = float(cat_score)
            except (TypeError, ValueError):
                s_val = 0
            cat_emoji = "🟢" if s_val >= 80 else "🟡" if s_val >= 50 else "🔴"
            bar = "█" * max(1, int(s_val / 10)) + "░" * max(0, 10 - int(s_val / 10))
            label = str(cat_name).replace("_", " ").title()
            lines.append(f"  {cat_emoji} <b>{esc(label)}</b>: {s_val:.0f}/100")
            lines.append(f"    <code>{bar}</code>")
        lines.append("")

    # Flags/warnings
    if flags:
        lines.append("⚠️ <b>RISK FLAGS</b>")
        lines.append(t)
        for flag in flags[:8]:
            if isinstance(flag, dict):
                sev = str(flag.get("severity", "medium"))
                msg = str(flag.get("message", flag.get("description", flag.get("flag", ""))))
                sev_emoji = severity_emoji(sev)
                lines.append(f"  {sev_emoji} {esc(msg[:120])}")
            else:
                lines.append(f"  🔴 {esc(str(flag)[:120])}")
        lines.append("")

    # ── Chunk 1 footer ──
    chunk1 = "\n".join(lines)

    # ── Chunk 2: Deployer + Bundle + Cluster + Wallet ──
    lines2: list[str] = []

    # Deployer Profile
    lines2.append("👨‍💻 <b>DEPLOYER PROFILE</b>")
    lines2.append(t)
    if deployer:
        dev_wallet = (
            deployer.get("dev_wallet") or deployer.get("creator") or deployer.get("address", "")
        )
        if dev_wallet:
            lines2.append(f"  Wallet: <code>{_short_addr(dev_wallet)}</code>")
        total = (
            deployer.get("total_launched")
            or deployer.get("token_count")
            or deployer.get("total_deployments")
        )
        rugs = deployer.get("total_rugged") or deployer.get("rug_count")
        rep = deployer.get("reputation") or deployer.get("risk_level")
        if total:
            lines2.append(f"  Tokens Launched: <b>{int(total):,}</b>")
        if rugs is not None:
            danger = " 🔴" if int(rugs) > 0 else " ✅"
            lines2.append(f"  Rugged/Dead: <b>{int(rugs):,}</b>{danger}")
        if rep:
            lines2.append(f"  Reputation: <b>{esc(str(rep))}</b>")
    elif dev_reputation:
        lines2.append(f"  Reputation Score: <b>{float(dev_reputation.get('score', 0)):.0f}/100</b>")
        rug_rate = dev_reputation.get("rug_rate")
        if rug_rate is not None:
            lines2.append(f"  Rug Rate: <b>{float(rug_rate) * 100:.0f}%</b>")
    else:
        lines2.append("  <i>No deployer data available</i>")
    lines2.append("")

    # Bundle Detection
    lines2.append("📦 <b>BUNDLE DETECTION</b>")
    lines2.append(t)
    if bundle_detected:
        lines2.append(
            f"  🔴 <b>BUNDLES DETECTED:</b> {bundle_wallets} wallets "
            f"({bundle_pct:.0f}%) appear bundled from common funding source"
        )
        lines2.append(
            "  ⚠️ <i>Suspicious concentration — possible wash trading or dev accumulation</i>"
        )
    else:
        lines2.append("  ✅ No bundle activity detected")
    lines2.append("")

    # Cluster Analysis
    lines2.append("🔗 <b>CLUSTER ANALYSIS</b>")
    lines2.append(t)
    if cluster_count > 0:
        lines2.append(f"  Wallet Clusters: <b>{cluster_count}</b> distinct clusters detected")
        if suspicious_clusters > 0:
            lines2.append(
                f"  🚨 <b>{suspicious_clusters} suspicious cluster(s)</b> — linked to deployer or funding source"
            )
        if cluster_details:
            for i, cluster in enumerate(cluster_details[:3], 1):
                c_name = cluster.get("name") or cluster.get("label") or f"Cluster {i}"
                c_size = (
                    cluster.get("size")
                    or cluster.get("wallet_count")
                    or len(cluster.get("wallets", []))
                )
                c_risk = (
                    "🔴" if cluster.get("suspicious") or cluster.get("deployer_linked") else "🟡"
                )
                lines2.append(f"  {c_risk} <b>{esc(str(c_name))}</b>: {c_size} wallets")
    else:
        lines2.append("  <i>No cluster data available</i>")
    lines2.append("")

    # Wallet Labels
    if wallet_labels:
        lines2.append("🏷️ <b>WALLET LABELS</b>")
        lines2.append(t)
        labels = wallet_labels.get("labels") or wallet_labels.get("known_labels") or []
        if isinstance(labels, list):
            for lbl in labels[:8]:
                if isinstance(lbl, dict):
                    lines2.append(f"  • {esc(str(lbl.get('label', lbl.get('name', str(lbl)))))}")
                else:
                    lines2.append(f"  • {esc(str(lbl))}")
        elif isinstance(labels, dict):
            for k, v in labels.items():
                lines2.append(f"  • {esc(k)}: {esc(str(v))}")
        else:
            lines2.append(f"  {esc(str(labels)[:200])}")
        lines2.append("")

    chunk2 = "\n".join(lines2)

    # ── Chunk 3: Sentinel Findings (if available) ──
    lines3: list[str] = []
    if sentinel_findings:
        lines3.append("🛡️ <b>SENTINEL ANALYSIS</b>")
        lines3.append(t)
        for finding in sentinel_findings[:8]:
            if isinstance(finding, dict):
                sev = str(finding.get("severity", "info"))
                title = finding.get("title") or finding.get("finding") or finding.get("label", "")
                desc = finding.get("description") or finding.get("detail") or ""
                sev_emoji = severity_emoji(sev)
                lines3.append(f"  {sev_emoji} <b>{esc(str(title)[:100])}</b>")
                if desc:
                    lines3.append(f"    {esc(str(desc)[:200])}")
            else:
                lines3.append(f"  • {esc(str(finding)[:200])}")
        lines3.append("")
    else:
        lines3.append(
            "🛡️ <b>SENTINEL ANALYSIS</b>\n"
            f"{t}\n"
            "  <i>SENTINEL deep forensic modules not yet available for this token.</i>\n"
            "  <i>Free modules: holder HHI, LP lock, dev reputation, metadata fingerprint, contract authority, proxy detection.</i>\n"
            "  <i>Premium modules (Scout+): bundle/sniper/wash-trading/MEV/sentiment/honeypot + 16 more.</i>\n"
        )
        lines3.append("")

    # Deal/Trade section
    lines3.append("💱 <b>TRADE ON:</b>")
    lines3.append(t)
    lines3.append(
        f'  🌐 <a href="{WEBSITE_URL}/scanner?token={address}">RugMunch.io Full Report</a>'
    )
    lines3.append("")

    lines3.append(footer_links())

    chunk3 = "\n".join(lines3)

    # Combine chunks, ensuring each is <= 4096
    result: list[str] = []
    for chunk in (chunk1, chunk2, chunk3):
        if chunk.strip():
            if len(chunk) <= 4000:
                result.append(chunk)
            else:
                # Split further at section boundaries if oversize
                sub_parts = chunk.split("\n\n")
                current = ""
                for part in sub_parts:
                    candidate = f"{current}\n\n{part}".strip() if current else part
                    if len(candidate) <= 4000:
                        current = candidate
                    else:
                        if current:
                            result.append(current)
                        current = part[:4000]
                if current:
                    result.append(current)

    return result


def _short_addr(addr: str) -> str:
    """Shorten an address for display."""
    if len(addr) > 12:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def format_alert(alert: dict) -> str:
    """Alert message — severity emoji, token, risk bar, reason."""
    severity = str(alert.get("severity") or "high")
    token = alert.get("token_symbol") or alert.get("symbol") or "???"
    address = alert.get("token_address") or alert.get("address") or ""
    chain = alert.get("chain") or "unknown"
    score = alert.get("risk_score") or 0
    reason = alert.get("reason") or alert.get("summary") or alert.get("message") or ""

    lines = [f"{severity_emoji(severity)} <b>Alert: {esc(token)}</b> ({esc(chain)})"]
    if address:
        lines.append(code(address))
    if score:
        lines.append("")
        lines.append(f"Risk: <b>{score:.0f}%</b>")
        lines.append(risk_bar(score))
    if reason:
        lines.append("")
        lines.append(blockquote(str(reason)))
    return "\n".join(lines)
