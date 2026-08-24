"""Whale convergence detector — win 33.

Flags tokens where MULTIPLE tracked smart-money wallets entered within
a tight window. One whale buying is noise; three independent smart
wallets converging on the same illiquid token inside an hour is a
coordinated accumulation signal — the strongest early tell in crypto.

Data: GMGN token-scoped smart money (top traders with PnL + buy counts).
Convergence = distinct wallets whose first-buy recency clusters.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

# Lazy import to avoid circular dep (scanner imports tools.__init__)

logger = logging.getLogger(__name__)

CONVERGENCE_WINDOW_MIN = 60
MIN_WALLETS = 3


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value)
            if ts > 1e12:
                ts /= 1000
            return datetime.fromtimestamp(ts, tz=UTC)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def detect_convergence(
    wallets: list[dict[str, Any]],
    *,
    window_min: int = CONVERGENCE_WINDOW_MIN,
    min_wallets: int = MIN_WALLETS,
) -> dict[str, Any] | None:
    """Detect clustered entries among smart-money wallet rows.

    Each row: {wallet, first_buy_ts, pnl, buy_count}. Returns a
    convergence report or None when under the threshold.
    """
    entries: list[tuple[datetime, dict[str, Any]]] = []
    for w in wallets:
        ts = _parse_ts(w.get("first_buy_ts") or w.get("bought_at"))
        if ts is None:
            continue
        entries.append((ts, w))
    if len(entries) < min_wallets:
        return None

    entries.sort(key=lambda x: x[0])
    best_cluster: list[tuple[datetime, dict[str, Any]]] = []
    for i in range(len(entries)):
        cluster = [entries[i]]
        for j in range(i + 1, len(entries)):
            if (entries[j][0] - entries[i][0]) <= timedelta(minutes=window_min):
                cluster.append(entries[j])
            else:
                break
        if len(cluster) > len(best_cluster):
            best_cluster = cluster
        if len(best_cluster) >= len(entries):
            break

    if len(best_cluster) < min_wallets:
        return None

    span_min = (
        (best_cluster[-1][0] - best_cluster[0][0]).total_seconds() / 60
        if len(best_cluster) > 1
        else 0.0
    )
    profitable = sum(
        1 for _, w in best_cluster if (_f(w.get("pnl")) or 0) > 0
    )
    return {
        "converging_wallets": len(best_cluster),
        "window_minutes": round(span_min, 1),
        "profitable_track_record": f"{profitable}/{len(best_cluster)}",
        "wallets": [
            {
                "wallet": str(w.get("wallet") or "")[:14] + "…",
                "pnl_usd": w.get("pnl"),
                "buys": w.get("buy_count"),
            }
            for _, w in best_cluster[:6]
        ],
        "read": (
            f"{len(best_cluster)} smart wallets converged within "
            f"{span_min:.0f}min — coordinated accumulation pattern"
        ),
    }


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


async def whale_convergence(*, address: str = "", chain: str = "eth") -> dict[str, Any]:
    """Tool entry: convergence analysis for one token."""
    if not address:
        return {"error": "token address required"}
    from rugmunchbot.tools.scanner._smart_money_token import fetch_token_smart_money

    data = await fetch_token_smart_money(address, chain)
    if not data:
        return {"error": "smart-money data unavailable", "address": address}
    wallets = data.get("wallets") or data.get("top_traders") or []
    conv = detect_convergence(wallets)
    out: dict[str, Any] = {
        "address": address,
        "chain": chain,
        "wallets_tracked": len(wallets),
        "convergence": conv,
    }
    if conv:
        out["headline"] = conv["read"]
        out["signal"] = "CONVERGENCE"
    else:
        out["headline"] = "no coordinated accumulation detected"
        out["signal"] = "NONE"
    return out
