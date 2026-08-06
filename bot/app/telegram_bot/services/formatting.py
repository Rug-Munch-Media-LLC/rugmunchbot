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


def format_v3_scan_report(r: dict) -> str:
    """Standardized beautiful scan report — v3 professional format."""
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
    pc_24h = _float_or(market.get("price_change_24h") or r.get("price_change_24h") or 0)

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

    # ── Tax formatting helpers ──
    def _tax_label(tax_val, which):
        try:
            v = float(tax_val)
            if v <= 1.0:
                v *= 100
            if v > 5:
                return f"🔴 {which} Tax: {v:.0f}%"
            elif v > 0.1:
                return f"⚠️ {which} Tax: {v:.0f}%"
            else:
                return f"✅ {which} Tax: {v:.0f}%"
        except Exception:
            return f"⚪ {which} Tax: N/A"

    def _verified_str(verified_val):
        if verified_val is True or verified_val == "yes":
            return "✅ Contract Verified"
        elif verified_val is False or verified_val == "no":
            return "🔴 Unverified"
        return "⚪ Verified N/A"

    def _lp_locked_str(lp_val):
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

    # ── Build report ──
    lines = [
        f"🛡️ <b>{esc(name)}</b> (${esc(symbol)})",
        sep(),
        f"💰 Price: {fmt_number(price):>10}  │  📊 MCap: {fmt_number(mcap)}",
        f"📈 24h: {esc(fmt_pct(pc_24h)):>10}  │  💧 Liq: {fmt_number(liq_usd)}",
        sep(),
        _safety_score_bar(risk),
    ]

    # Security row
    lines.append(f"{_verified_str(verified)}  │  {_lp_locked_str(lp_locked)}")
    lines.append(f"{_tax_label(buy_tax, 'Buy')}  │  {_tax_label(sell_tax, 'Sell')}")

    lines.extend(
        [
            sep(),
            f"👥 Holders: {holder_count:,}  │  🐋 Top 10: {_pct_str(top10)}",
            f"🏷️ Chain: {esc(chain).upper():<10}  │  📅 Age: {age or 'N/A'}",
            sep(),
        ]
    )

    # Threats/warnings
    warnings = r.get("warnings") or r.get("factors") or r.get("threats") or []
    if warnings:
        lines.append("")
        for w in warnings[:4]:
            lines.append(f"  🔴 {esc(str(w)[:100])}")

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
    """SENTINEL deep forensic report with holder breakdown and RAG insight."""
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

    s = sep()
    t = thin_sep()

    lines = ["🧠 <b>SENTINEL Deep Forensic Report</b>", s]
    if address:
        lines.append(code(address))
    lines.extend(["", risk_bar(score)])
    if summary:
        lines.append("")
        lines.append(blockquote(str(summary)))

    # ── Deep analysis section ──
    lines.extend(
        [
            "",
            "🔬 <b>DEEP ANALYSIS</b>",
            s,
            f"🔄 Bundle Detection: {int(bundle_count):,} wallets ({_pct_str(bundle_pct)})",
            f"🕵️ Sniper Activity: {int(sniper_count):,} snipers",
            f"🔗 Dev Connections: {int(dev_links):,} linked wallets",
            f"💀 Rug Probability: {_pct_str(rug_prob)}",
        ]
    )

    # ── Holder breakdown ──
    if any([whales, sharks, fish, shrimp]):
        lines.extend(
            [
                "",
                "📊 <b>HOLDER BREAKDOWN</b>",
                s,
                f"🐋 Whales (>1%): {int(whales):,} ({_pct_str(whales_pct)})  │  🦈 Sharks: {int(sharks):,} ({_pct_str(sharks_pct)})",
                f"🐟 Fish: {int(fish):,} ({_pct_str(fish_pct)})  │  🐠 Shrimp: {int(shrimp):,} ({_pct_str(shrimp_pct)})",
            ]
        )
    else:
        lines.extend(
            ["", "📊 <b>HOLDER BREAKDOWN</b>", s, "⚠️ Detailed holder breakdown not available"]
        )

    # ── RAG insight ──
    if rag:
        lines.extend(
            [
                "",
                "🤖 <b>RAG INSIGHT</b>",
                s,
                blockquote(str(rag)[:500]),
            ]
        )
    else:
        lines.extend(
            [
                "",
                "🤖 <b>RAG INSIGHT</b>",
                s,
                "<i>No similar token patterns found in knowledge base.</i>",
            ]
        )

    # ── Findings ──
    if findings:
        lines.extend(["", "🔬 <b>Findings</b>", t])
        for item in findings[:10]:
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
            lines.extend(["", f"🔎 <b>{key.title()}</b>", t])
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
