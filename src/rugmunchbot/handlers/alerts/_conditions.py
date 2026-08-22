"""Alert engine pure logic — trigger conditions + monitor security deltas.

No I/O, no Telegram, no DB. Extracted verbatim from the former single-module
``handlers/alerts.py``.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot")


def _check_alert_condition(current_price: float, alert_price: float, direction: str) -> bool:
    if current_price <= 0 or alert_price <= 0:
        return False
    if direction == "above":
        return current_price >= alert_price
    elif direction == "below":
        return current_price <= alert_price
    elif direction == "change_5":
        return abs((current_price - alert_price) / alert_price) * 100 >= 5.0
    elif direction == "any":
        return abs((current_price - alert_price) / alert_price) * 100 >= 2.0
    return False


def _monitor_baseline(result: dict[str, Any]) -> dict[str, Any]:
    """Project a scan result onto the security properties a monitor watches.

    Mirrors the fields the scanner populates (``tools/scanner.scan_token``):
    tax fractions, ownership-renounced flag, mint authority, LP lock fraction
    and honeypot state. Absent values stay None — never fabricated.
    """
    return {
        "buy_tax": result.get("buy_tax"),
        "sell_tax": result.get("sell_tax"),
        "owner_renounced": result.get("owner_renounced"),
        "mintable": result.get("mintable"),
        "lp_locked": result.get("lp_locked"),
        "honeypot": result.get("honeypot"),
    }


def _pct(v) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (ValueError, TypeError):
        return "unknown"


def _monitor_deltas(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Return human-readable change lines between two security snapshots."""
    changes: list[str] = []
    for field in ("buy_tax", "sell_tax"):
        before, after = baseline.get(field), current.get(field)
        if before is None or after is None:
            continue
        try:
            if abs(float(after) - float(before)) >= 0.01:
                changes.append(
                    f"{field.replace('_', ' ').title()}: {_pct(before)} \u2192 {_pct(after)}"
                )
        except (ValueError, TypeError):
            continue
    for field, label in (
        ("owner_renounced", "Ownership renounced"),
        ("mintable", "Mint authority"),
        ("honeypot", "Honeypot"),
    ):
        before, after = baseline.get(field), current.get(field)
        if before is None or after is None:
            continue
        if bool(before) != bool(after):
            changes.append(f"{label}: {bool(before)} \u2192 {bool(after)}")
    before_lp, after_lp = baseline.get("lp_locked"), current.get("lp_locked")
    if before_lp is not None and after_lp is not None:
        try:
            if float(after_lp) <= float(before_lp) - 0.2:
                changes.append(f"LP locked: {_pct(before_lp)} \u2192 {_pct(after_lp)}")
        except (ValueError, TypeError) as e:
            logger.debug("lp delta parse failed: %s", e)
    return changes
