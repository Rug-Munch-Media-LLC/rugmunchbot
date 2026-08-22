#!/usr/bin/env python3
"""Risk visualization and number/chain formatting helpers."""

from __future__ import annotations

# ══════════════════════════════════════════════
# RISK VISUALIZATION
# ══════════════════════════════════════════════
RISK_COLORS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
    "safe": "✅",
    "unknown": "⚪",
}


def risk_bar(score: float, length: int = 10) -> str:
    """Generate a visual risk bar (0-100 scale)."""
    if score is None:
        score = 0.0
    score = float(score)
    filled = int((score / 100) * length)
    if score >= 80:
        return "🔴" * filled + "⬜" * (length - filled) + f" {score}% DANGER"
    elif score >= 60:
        return "🟠" * filled + "⬜" * (length - filled) + f" {score}% RISKY"
    elif score >= 40:
        return "🟡" * filled + "⬜" * (length - filled) + f" {score}% CAUTION"
    elif score >= 20:
        return "🟢" * filled + "⬜" * (length - filled) + f" {score}% LOW"
    else:
        return "✅" * filled + "⬜" * (length - filled) + f" {score}% SAFE"


def _risk_gauge(score: float) -> str:
    """Compact emoji risk gauge for scan results."""
    if score is None:
        score = 0.0
    if score >= 80:
        return "🔴🔴🔴🔴🔴 EXTREME RISK"
    elif score >= 60:
        return "🟠🟠🟠🟠⬜ HIGH RISK"
    elif score >= 40:
        return "🟡🟡🟡⬜⬜ MODERATE"
    elif score >= 20:
        return "🟢🟢⬜⬜⬜ LOW RISK"
    else:
        return "✅✅✅✅✅ SAFE"


def threat_indicator(label: str, found: bool) -> str:
    return f"{'🔴' if found else '✅'} {label}"


# ══════════════════════════════════════════════
# FORMATTING HELPERS
# ══════════════════════════════════════════════


def fmt_number(n) -> str:
    """Format large numbers: 1.2M, 450K, etc."""
    if n is None:
        return "N/A"
    try:
        n = float(n)
    except (ValueError, TypeError):
        return str(n)
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    elif n >= 1e6:
        return f"${n / 1e6:.2f}M"
    elif n >= 1e3:
        return f"${n / 1e3:.1f}K"
    elif n >= 1:
        return f"${n:.4f}"
    elif n >= 0.01:
        return f"${n:.6f}"
    else:
        return f"${n:.10f}"


def fmt_pct(n) -> str:
    if n is None:
        return "N/A"
    try:
        return f"{float(n):.2f}%"
    except (ValueError, TypeError):
        return str(n)


def fmt_chain(chain: str) -> str:
    """Return chain emoji + name."""
    emojis = {
        "solana": "◎",
        "ethereum": "⟠",
        "bsc": "🔶",
        "polygon": "🟣",
        "arbitrum": "🔵",
        "optimism": "🔴",
        "base": "🔷",
        "avalanche": "🔺",
        "fantom": "👻",
        "tron": "🔻",
        "bitcoin": "₿",
    }
    return f"{emojis.get(chain.lower(), '⛓️')} {chain.capitalize()}"
