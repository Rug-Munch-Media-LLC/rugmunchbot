"""Token-scoped smart-money enrichment — fixes the wallet/token endpoint mixup.

The vendored ``gmgn_enrich`` calls GMGN's **wallet-scoped** smart-money
endpoint (``/smartmoney/{chain}/walletNew/{wallet}``) with a **token**
address, which can only return empty/garbage rows — every report then shows
a fabricated "Smart-Money Net: +0". This module queries the correct
**token-scoped** endpoints (``tokenNew`` / ``token`` variants), normalizes
the payload defensively, and merges the result into
``result["gmgn"]["smart_money"]`` so the formatter renders real tracked-
wallet flow.

Fail-open contract identical to the rest of the scan pipeline: any error,
timeout (hard 6s budget) or empty payload leaves the scan result untouched.
"""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.tools.scanner.smart_money_token")

_SMART_MONEY_TIMEOUT_S = 6.0

# Token-scoped endpoint path candidates, tried in order. GMGN has shipped
# both spellings across API revisions; both are attempted before giving up.
_TOKEN_PATHS = ("tokenNew", "token")


def _chain_slug(chain: str) -> str:
    """Normalize a chain name to GMGN's slug (sol → solana)."""
    c = (chain or "solana").lower()
    return {"sol": "solana", "bsc": "bsc", "eth": "eth", "base": "base"}.get(c, c)


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_wallet(row: dict[str, Any]) -> str:
    for key in ("wallet", "wallet_address", "address", "maker"):
        val = row.get(key)
        if isinstance(val, str) and val:
            return val
    return ""


def _row_counts(row: dict[str, Any]) -> tuple[float | None, float | None]:
    buys = _to_float(
        row.get("buy_7d", row.get("buy_count", row.get("buy_tx_count", row.get("buy"))))
    )
    sells = _to_float(
        row.get("sell_7d", row.get("sell_count", row.get("sell_tx_count", row.get("sell"))))
    )
    return buys, sells


def _normalize(data: dict[str, Any], address: str, chain: str) -> dict[str, Any] | None:
    """Normalize a token-scoped smart-money payload into the report shape."""
    raw_inner = data.get("data")
    inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else data

    activity = inner.get("activity") or inner.get("smart_degens") or []
    if not isinstance(activity, list):
        activity = []

    wallets: list[dict[str, Any]] = []
    net = 0.0
    counted = 0
    for row in activity[:50]:
        if not isinstance(row, dict):
            continue
        addr = _row_wallet(row)
        buys, sells = _row_counts(row)
        pnl = _to_float(row.get("realized_profit", row.get("pnl")))
        if addr:
            entry: dict[str, Any] = {}
            if buys is not None and sells is not None:
                delta = buys - sells
                net += delta
                counted += 1
                entry = {
                    "address": addr,
                    "net": round(delta, 2),
                    "direction": "BUY" if delta > 0 else "SELL" if delta < 0 else "MIXED",
                }
            elif pnl is not None:
                # Rows with PnL but no tx counts still prove smart presence.
                entry = {
                    "address": addr,
                    "net": 0.0,
                    "direction": "MIXED",
                    "pnl_usd": round(pnl, 2),
                }
            if entry:
                wallets.append(entry)

    sm_count = int(
        _to_float(inner.get("smart_degen_count", inner.get("smart_money_count"))) or counted or 0
    )
    kol_count = int(_to_float(inner.get("kol_count")) or 0)
    buy_pressure = _to_float(inner.get("buy_pressure"))
    total_pnl = _to_float(inner.get("total_profit_pnl"))

    if not sm_count and not kol_count and not wallets and buy_pressure is None:
        return None

    # Fall back to GMGN's aggregate buy_pressure ONLY when no per-wallet
    # rows were countable — with real rows, a zero net is a genuine balance,
    # never missing data.
    if counted == 0 and buy_pressure:
        net = buy_pressure

    return {
        "net_bought": round(net, 2),
        "smart_money_count": int(sm_count or len(wallets)),
        "kol_count": kol_count,
        "buy_pressure": buy_pressure,
        "total_profit_pnl": total_pnl,
        "top_smart_money_wallets": wallets[:3],
        "source": "gmgn_token_scoped",
        "chain": chain,
        "address": address,
    }


async def fetch_token_smart_money(address: str, chain: str) -> dict[str, Any] | None:
    """Fetch token-scoped smart-money data from GMGN (fail-open).

    Returns the normalized report-shape dict, or ``None`` when GMGN is
    unavailable / returns nothing usable.
    """
    try:
        provider_mod = __import__(
            "rugmunchbot._vendor.gmgn_provider", fromlist=["get_gmgn_provider"]
        )
        provider = provider_mod.get_gmgn_provider()
    except ImportError:
        return None
    if getattr(provider, "_available", True) is False:
        return None

    slug = _chain_slug(chain)
    request = getattr(provider, "_request", None)
    headers = getattr(provider, "headers", {})
    if request is None:
        return None

    async def _try(path: str) -> dict[str, Any] | None:
        try:
            resp = await request(
                "GET",
                f"{provider.GMGN_BASE}/defi/quotation/v1/smartmoney/{slug}/{path}/{address}",
                headers=headers,
                params={"period": "7d"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.debug("gmgn token smart money %s failed: %s", path, exc)
            return None

    for path in _TOKEN_PATHS:
        try:
            data = await asyncio.wait_for(_try(path), timeout=_SMART_MONEY_TIMEOUT_S)
        except Exception:  # noqa: S112 — try next provider path on failure
            continue
        if isinstance(data, dict):
            normalized = _normalize(data, address, chain)
            if normalized:
                return normalized
    return None


async def enrich_scan_smart_money(
    result: dict[str, Any], address: str, chain: str
) -> dict[str, Any]:
    """Merge token-scoped smart money into ``result["gmgn"]["smart_money"]``.

    Overrides the wallet-scoped garbage (net always 0) with real flow when
    available; never touches an already-good payload.
    """
    if not isinstance(result, dict) or not address:
        return result
    existing = (result.get("gmgn") or {}).get("smart_money")
    if isinstance(existing, dict) and existing.get("source") == "gmgn_token_scoped":
        return result  # already enriched by this module
    try:
        data = await asyncio.wait_for(
            fetch_token_smart_money(address, chain), timeout=_SMART_MONEY_TIMEOUT_S
        )
    except Exception:
        return result
    if not data:
        return result
    gmgn = result.setdefault("gmgn", {})
    if isinstance(gmgn, dict):
        gmgn["smart_money"] = data
    return result


__all__ = ["enrich_scan_smart_money", "fetch_token_smart_money"]
