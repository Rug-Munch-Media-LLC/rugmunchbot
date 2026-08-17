# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/gmgn_enrich.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""GMGN holder / smart-money enrichment for the RugMunchBot scan report.

Best-effort, fail-open enrichment: fetches GMGN holder analysis + smart
money data and attaches a compact ``gmgn`` sub-dict to the scan result so
the Telegram formatter can render a "Smart Money / Holders" section. Any
error (provider failure, bad payload) or timeout (hard 6s budget) returns
the scan result UNCHANGED — a slow GMGN call can never block or fail a
scan.

Normalized shape attached under ``scan_result["gmgn"]``::

    {
        "holders": {
            "top_holder_pct": float | None,   # top-10 supply % (GMGN top list or Birdeye)
            "holder_count": int | None,        # holder count reported by the provider
            "insider_count": int,
            "sniper_count": int,
            "source": "gmgn" | "birdeye_fallback",
        },
        "smart_money": {
            "net_bought": float,               # net buy-sell tx count (derived)
            "smart_money_count": int,
            "kol_count": int,
            "buy_pressure": float | None,
            "total_profit_pnl": float | None,
            "top_smart_money_wallets": [
                {"address": str, "net": float, "direction": "BUY"|"SELL"|"MIXED"},
            ],
            "source": "gmgn" | "none",
        },
    }

Field names were read from ``app/databus/gmgn_provider.py``
(``get_holder_analysis`` / ``get_smart_money``) — see
``tests/test_gmgn_enrich.py`` for the exact payload fixtures.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot._vendor.gmgn_provider import get_gmgn_provider

# Hard budget for the whole enrichment (both fetches). GMGN's httpx client
# times out at 20s; we never let that block the scan pipeline.
_GMGN_TIMEOUT_S = 6.0

# Maximum smart-money wallet moves surfaced in the report section.
_MAX_WALLETS = 3


def _to_float(value: Any) -> float | None:
    """Coerce a value to float, returning None for missing/unparseable."""
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _sum_top_pct(holders: Any, cap: int = 10) -> float | None:
    """Sum supply % of the first ``cap`` top holders (GMGN rows)."""
    if not isinstance(holders, list):
        return None
    total = 0.0
    seen = 0
    for h in holders:
        if not isinstance(h, dict):
            continue
        pct = _to_float(h.get("amount_percentage"))
        if pct is None:
            continue
        total += pct
        seen += 1
        if seen >= cap:
            break
    return total if seen else None


def _wallet_addr(row: dict[str, Any]) -> str:
    """Extract a wallet address from a GMGN activity row (defensive keys)."""
    for key in ("wallet", "wallet_address", "address", "maker"):
        val = row.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _row_buys_sells(row: dict[str, Any]) -> tuple[float | None, float | None]:
    """Pull buy/sell counts from a GMGN activity row (defensive keys)."""
    buys = _to_float(row.get("buy_7d", row.get("buy_count", row.get("buy_tx_count"))))
    sells = _to_float(row.get("sell_7d", row.get("sell_count", row.get("sell_tx_count"))))
    return buys, sells


def _net_from_activity(activity: Any) -> float:
    """Net buy-sell tx count across smart-money activity rows."""
    if not isinstance(activity, list):
        return 0.0
    total = 0.0
    for row in activity[:50]:
        if not isinstance(row, dict):
            continue
        buys, sells = _row_buys_sells(row)
        if buys is not None and sells is not None:
            total += buys - sells
    return total


def _top_smart_money_wallets(activity: Any, limit: int = _MAX_WALLETS) -> list[dict[str, Any]]:
    """Pick the first ``limit`` activity rows that carry a wallet + direction."""
    wallets: list[dict[str, Any]] = []
    if not isinstance(activity, list):
        return wallets
    for row in activity:
        if not isinstance(row, dict):
            continue
        addr = _wallet_addr(row)
        if not addr:
            continue
        buys, sells = _row_buys_sells(row)
        if buys is None or sells is None:
            continue
        net = buys - sells
        direction = "BUY" if net > 0 else "SELL" if net < 0 else "MIXED"
        wallets.append({"address": addr, "net": round(net, 2), "direction": direction})
        if len(wallets) >= limit:
            break
    return wallets


def _normalize_holders(holders_data: Any) -> dict[str, Any]:
    """Normalize ``get_holder_analysis`` output into the report's holders dict."""
    if not isinstance(holders_data, dict):
        return {}
    top_holder_pct = _to_float(holders_data.get("top_10_pct"))
    if top_holder_pct is None:
        # GMGN rows carry per-wallet amount_percentage; Birdeye fallback
        # provides top_10_pct directly.
        top_holder_pct = _sum_top_pct(holders_data.get("top_holders"))
    holder_count = holders_data.get("total_holders")
    return {
        "top_holder_pct": top_holder_pct,
        "holder_count": int(holder_count) if isinstance(holder_count, (int, float)) else None,
        "insider_count": int(holders_data.get("insider_count") or 0),
        "sniper_count": int(holders_data.get("sniper_count") or 0),
        "source": str(holders_data.get("source") or "unknown"),
    }


def _normalize_smart_money(sm_data: Any) -> dict[str, Any]:
    """Normalize ``get_smart_money`` output into the report's smart-money dict."""
    if not isinstance(sm_data, dict):
        return {}
    activity = sm_data.get("smart_money_activity") or []
    net_bought = _net_from_activity(activity)
    # GMGN does not expose an explicit net-flow figure for the token; fall
    # back to ``buy_pressure`` when the activity rows carry no buy/sell
    # counts (buy_pressure is a signed aggregate GMGN reports).
    if net_bought == 0:
        bp = _to_float(sm_data.get("buy_pressure"))
        if bp is not None:
            net_bought = bp
    return {
        "net_bought": round(net_bought, 2),
        "smart_money_count": int(sm_data.get("smart_money_count") or 0),
        "kol_count": int(sm_data.get("kol_count") or 0),
        "buy_pressure": _to_float(sm_data.get("buy_pressure")),
        "total_profit_pnl": _to_float(sm_data.get("total_profit_pnl")),
        "top_smart_money_wallets": _top_smart_money_wallets(activity),
        "source": str(sm_data.get("source") or "unknown"),
    }


async def enrich_scan_with_gmgn(scan_result: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Attach GMGN holder/smart-money enrichment to a scan result (fail-open).

    Returns the scan result UNCHANGED on any error or when the combined
    fetch exceeds ``_GMGN_TIMEOUT_S`` seconds — enrichment is best-effort
    and must never block or fail the scan it decorates.
    """
    if not isinstance(scan_result, dict) or not address:
        return scan_result
    try:
        provider = get_gmgn_provider()
        # ``return_exceptions=True`` means a failed fetch surfaces as an
        # exception *value* instead of raising — each slot is normalized to
        # None below so a partial failure still yields the other source.
        holders_data: Any
        sm_data: Any
        holders_data, sm_data = await asyncio.wait_for(
            asyncio.gather(
                provider.get_holder_analysis(address, chain),
                provider.get_smart_money(address, chain),
                return_exceptions=True,
            ),
            timeout=_GMGN_TIMEOUT_S,
        )
        if isinstance(holders_data, BaseException):
            holders_data = None
        if isinstance(sm_data, BaseException):
            sm_data = None

        gmgn: dict[str, Any] = {}
        holders = _normalize_holders(holders_data)
        smart = _normalize_smart_money(sm_data)
        if holders:
            gmgn["holders"] = holders
        if smart:
            gmgn["smart_money"] = smart
        if gmgn:
            scan_result["gmgn"] = gmgn
    except Exception:  # noqa: S110  # best-effort enrichment, never raises
        # Fail-open contract: leave the scan result untouched. Intentionally
        # silent — GMGN enrichment is optional and must never fail a scan.
        pass
    return scan_result
