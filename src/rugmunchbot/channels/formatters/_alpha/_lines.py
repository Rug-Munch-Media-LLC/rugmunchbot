"""Small value formatters shared by the alpha/scan card renderers.

Pure functions only — no network I/O. Split out of ``_alpha.py`` (2026-10-23).
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.formatters import short_addr

#: Per-chain tx explorer host (hostname, no scheme/path) for whale-move links.
_TX_EXPLORER: tuple[tuple[str, str], ...] = (
    ("solana", "https://solscan.io/tx/"),
    ("ethereum", "https://etherscan.io/tx/"),
    ("base", "https://basescan.org/tx/"),
    ("arbitrum", "https://arbiscan.io/tx/"),
    ("bsc", "https://bscscan.com/tx/"),
    ("polygon", "https://polygonscan.com/tx/"),
    ("avalanche", "https://snowtrace.io/tx/"),
)

_HOUR_IN_MINUTES = 60
_EPOCH_MILLIS_THRESHOLD = 1e12


def _fmt_usd(value: Any) -> str:
    with suppress(TypeError, ValueError):
        return f"${float(value):,.0f}"
    return ""


def _fmt_age_hours(value: Any) -> str:
    with suppress(TypeError, ValueError):
        hours = float(value)
        if hours < 1:
            return f"⏱ {int(hours * _HOUR_IN_MINUTES)}m old"
        return f"⏱ {hours:.0f}h old"
    return ""


def _fmt_wallet_short(addr: str) -> str:
    return short_addr(addr) if addr else "?"


def _tx_url(chain: str, tx_hash: str) -> str:
    """Per-chain transaction explorer URL ("" when unknown)."""
    tx_hash = (tx_hash or "").strip()
    if not tx_hash:
        return ""
    for c, prefix in _TX_EXPLORER:
        if c == (chain or "").lower():
            return f"{prefix}{tx_hash}"
    return ""


def _fmt_move_time(value: Any) -> str:
    """Readable timestamp from a whale move (ISO or epoch; "" when absent)."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.replace(".", "", 1).isdigit():  # epoch seconds/millis
        try:
            ts = float(raw)
            if ts > _EPOCH_MILLIS_THRESHOLD:
                ts /= 1000.0
            return f"🕐 {datetime.fromtimestamp(ts, tz=UTC).strftime('%b %d, %H:%M UTC')}"
        except (ValueError, OSError):
            return f"🕐 {raw}"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return f"🕐 {dt.strftime('%b %d, %H:%M UTC')}"
    except ValueError:
        return f"🕐 {raw}"


def _launch_verdict(launch: dict[str, Any]) -> str:
    """One-line "why it matters" for a launch, honest to its scan verdict."""
    risk = str(launch.get("risk_level") or "unknown").lower()
    if risk == "critical":
        return "critical scan verdict — skip unless you know exactly what you are doing"
    if risk == "high":
        return "high scan verdict — verify liquidity and holders before sizing"
    if risk == "medium":
        return "medium scan verdict — check liquidity and holder concentration"
    if risk == "low":
        return "low scan verdict — still verify before buying"
    return "new launch — verify before buying"
