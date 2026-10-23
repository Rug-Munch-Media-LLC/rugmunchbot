"""Smart-money moves feed.

Source chain (first non-empty wins):

1. ``/api/v1/smart-money/moves`` — the canonical moves contract. It is NOT
   mounted in rmi-backend today (404 observed 2026-10-23), so this probe
   documents the gap instead of hiding it.
2. ``/api/v1/databus/premium/smart-money`` — live DataBus smart-money feed
   (``smart_money_wallets``).
3. ``/api/v1/market/smart-money`` — market-intel surface (stub, empty today).

No row is rendered unless it carries a real symbol; missing numbers stay
missing (the renderer prints ``n/a``), never zero-filled.
"""

from __future__ import annotations

from typing import Any

from ._http import backend_json
from .types import Feed

_SOURCES: tuple[tuple[str, str], ...] = (
    ("/api/v1/smart-money/moves", "smart-money/moves"),
    ("/api/v1/databus/premium/smart-money", "databus.smart_money"),
    ("/api/v1/market/smart-money", "market.smart_money"),
)
_ROWS = ("items", "results", "moves", "smart_money_wallets", "wallets", "data")
_WALLET = ("wallet", "address", "trader", "wallet_address", "from_wallet")
_SYMBOL = ("token_symbol", "symbol", "asset", "token", "token_ticker")
_AMOUNT = ("amount_usd", "value_usd", "usd_value", "volume_usd", "amount")
_ACTION = ("action", "side", "tx_type", "direction", "kind")
_PNL = ("pnl_usd", "realized_pnl_usd", "profit_usd")


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", []):
            return value
    return None


def _rows(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if not isinstance(body, dict):
        return []
    for key in _ROWS:
        value = body.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


def _normalise(row: dict[str, Any], chain_hint: str) -> dict[str, Any] | None:
    symbol = _first(row, _SYMBOL)
    if symbol is None:
        return None
    return {
        "wallet": _first(row, _WALLET),
        "symbol": str(symbol),
        "chain": row.get("chain") or row.get("network") or chain_hint,
        "amount_usd": _first(row, _AMOUNT),
        "action": str(_first(row, _ACTION) or "move").lower(),
        "pnl_usd": _first(row, _PNL),
        "token_address": row.get("token_address") or row.get("mint"),
        "tx_hash": row.get("tx_hash") or row.get("signature") or row.get("tx"),
        "entity": row.get("entity") or row.get("label") or row.get("wallet_label"),
    }


async def fetch_moves(limit: int = 10, chain: str = "solana") -> Feed:
    """Fetch normalised smart-money moves, or an honest empty Feed."""
    tried: list[str] = []
    for path, label in _SOURCES:
        tried.append(label)
        params: dict[str, Any] = {"limit": limit}
        if "databus" in path:
            params["chain"] = chain
        body = await backend_json(path, params=params)
        rows = [_normalise(r, chain) for r in _rows(body)]
        items = tuple(r for r in rows[:limit] if r)
        if items:
            updated = (body or {}).get("scan_time") or (body or {}).get("updated_at") or ""
            return Feed(
                kind="moves",
                items=items,
                source=label,
                updated_at=str(updated),
                tried=tuple(tried),
            )
    return Feed(
        kind="moves",
        note="no smart-money rows returned by any configured source",
        tried=tuple(tried),
    )
