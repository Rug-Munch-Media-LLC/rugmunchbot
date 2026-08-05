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
    fmt_chain,
    fmt_number,
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


def format_v3_scan_report(r: dict) -> str:
    """Full scan report (v2 contract shape with legacy flat fallbacks)."""
    name = r.get("name") or "Token"
    symbol = r.get("symbol") or r.get("ticker") or "???"
    chain = r.get("chain") or "unknown"
    address = r.get("address") or r.get("token_address") or ""
    risk = r.get("risk_score") or 0
    tier = r.get("risk_tier") or risk_tier(risk)
    warnings = r.get("warnings") or r.get("factors") or r.get("threats") or []
    holders = r.get("holders") or {}
    deployer = r.get("deployer") or {}
    contract = r.get("contract") or {}

    lines = [
        "🛡️ <b>RMI Token Scan</b>",
        sep(),
        f"<b>{esc(name)}</b> ({esc(symbol)}) {fmt_chain(chain)}",
    ]
    if address:
        lines.append(code(address))
    lines.append("")
    lines.extend(_market_line(r))
    lines.extend(["", "🔒 <b>Security</b>", thin_sep(), risk_bar(risk)])
    lines.append("")
    lines.extend(_warnings_block(warnings, tier))

    # Contract details
    c_lines = []
    if contract.get("verified") is not None:
        c_lines.append(
            f"{'✅' if contract['verified'] else '🔴'} Verified"
            if contract["verified"]
            else "🔴 Contract not verified"
        )
    if contract.get("mintable"):
        c_lines.append("🔴 Mintable supply")
    if contract.get("owner_renounced") is False:
        c_lines.append("🔴 Ownership not renounced")
    if contract.get("buy_tax_pct"):
        c_lines.append(f"📥 Buy Tax: {contract['buy_tax_pct']:.1f}%")
    if contract.get("sell_tax_pct"):
        c_lines.append(f"📤 Sell Tax: {contract['sell_tax_pct']:.1f}%")
    if c_lines:
        lines.extend(["", "📜 <b>Contract</b>", thin_sep()])
        lines.extend(c_lines)

    # Holders + deployer
    adv = []
    if holders.get("top10_pct") is not None:
        adv.append(f"👥 Top10 holders: <b>{holders['top10_pct']:.1f}%</b>")
    if holders.get("fresh_wallet_pct") is not None:
        adv.append(f"🆕 Fresh wallets: <b>{holders['fresh_wallet_pct']:.1f}%</b>")
    if deployer:
        other = deployer.get("other_tokens") or 0
        rep = deployer.get("reputation") or "unknown"
        adv.append(f"🕸️ Deployer: <b>{other}</b> other tokens ({esc(rep)})")
        if deployer.get("address"):
            adv.append(f"👨‍💻 Deployer: {code(deployer['address'])}")
    if adv:
        lines.extend(["", "🔬 <b>Advanced</b>", thin_sep()])
        lines.extend(adv)

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
    """SENTINEL deep forensic report (defensive over unknown API shape)."""
    address = r.get("address") or r.get("token_address") or ""
    score = r.get("risk_score") or r.get("score") or 0
    summary = r.get("summary") or r.get("verdict") or r.get("result") or ""
    findings = r.get("findings") or r.get("warnings") or r.get("flags") or []

    lines = ["🧠 <b>SENTINEL Deep Forensic Report</b>", sep()]
    if address:
        lines.append(code(address))
    lines.extend(["", risk_bar(score)])
    if summary:
        lines.append("")
        lines.append(blockquote(str(summary)))
    if findings:
        lines.extend(["", "🔬 <b>Findings</b>", thin_sep()])
        for item in findings[:10]:
            if isinstance(item, dict):
                sev = item.get("severity") or item.get("level") or "info"
                label = item.get("title") or item.get("finding") or item.get("label") or str(item)
                lines.append(f"  {severity_emoji(str(sev))} {esc(label)}")
            else:
                lines.append(f"  • {esc(item)}")
    # Pass-through sections (holders/liquidity/deployer/market/timeline…)
    for key in ("holders", "liquidity", "deployer", "market", "contract", "timeline"):
        section = r.get(key)
        if isinstance(section, dict) and section:
            lines.extend(["", f"🔎 <b>{key.title()}</b>", thin_sep()])
            for k, v in section.items():
                label = str(k).replace("_", " ").title()
                lines.append(f"  {esc(label)}: <b>{esc(v)}</b>")
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
