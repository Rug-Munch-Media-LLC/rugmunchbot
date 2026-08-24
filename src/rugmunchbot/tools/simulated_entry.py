"""Simulated entry/exit — "what would $1k have done?" (win 40).

Back-computes historical prices from DexScreener multi-timeframe price
changes and simulates a fixed entry. Honest by construction: uses only
the change percentages the scan already carries; when a window is
missing, that row renders as unavailable rather than fabricated.

Note: wash-traded tokens make this line misleading by design — the
volume_integrity verdict gates whether we show it at all.
"""

from __future__ import annotations

from typing import Any

_ENTRY_USD = 1000.0

# window label -> (result key, hours back)
_WINDOWS: list[tuple[str, str, float]] = [
    ("1h", "price_change_1h", 1.0),
    ("6h", "price_change_6h", 6.0),
    ("24h", "price_change_24h", 24.0),
]


def simulate_entry(price: float, changes: dict[str, Any]) -> dict[str, Any]:
    """Simulate a $1k entry at each available lookback window.

    Returns {window: {"entry_price", "now_worth_usd", "pnl_pct"}} for
    windows with data; unavailable windows are omitted (never faked).
    """
    out: dict[str, Any] = {}
    if price <= 0:
        return out
    for label, key, _hours in _WINDOWS:
        chg = changes.get(key)
        if chg is None:
            continue
        try:
            pct = float(chg)
        except (TypeError, ValueError):
            continue
        # price_now = price_then * (1 + pct/100)  =>  price_then = now / factor
        factor = 1 + pct / 100
        if factor <= 0:
            continue  # token went to zero in this window; can't back-compute
        entry_price = price / factor
        out[label] = {
            "entry_price": round(entry_price, 10 if entry_price < 1 else 4),
            "now_worth_usd": round(_ENTRY_USD * factor, 2),
            "pnl_pct": round(pct, 2),
        }
    return out


def render_simulated_line(sim: dict[str, Any], *, wash_verdict: str | None = None) -> str:
    """One HTML line for the scan card, or "" when gated/unavailable."""
    if not sim:
        return ""
    if wash_verdict in ("LIKELY_WASHED", "SUSPECT"):
        return (
            '<i>💰 $1k simulation withheld — volume integrity '
            f"{wash_verdict.lower().replace('_', ' ')}</i>"
        )
    parts = []
    for label in ("1h", "6h", "24h"):
        w = sim.get(label)
        if not w:
            continue
        pnl = w["pnl_pct"]
        arrow = "🟢" if pnl >= 0 else "🔴"
        parts.append(f"{arrow} {label}: ${w['now_worth_usd']:,.0f}")
    if not parts:
        return ""
    return "💰 <b>$1k entry simulation</b>: " + " · ".join(parts)
