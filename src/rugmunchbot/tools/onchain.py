"""top_traders + smart_money tools — who's buying what, REAL on-chain data.

Backend sources (own-data first):
  * ``/api/v1/databus/token/{address}`` — includes ``top_holders`` /
    ``smart_money`` / ``gmgn`` enrichment when available (GMGN holders +
    smart-money flags, Birdeye, DexScreener).
  * ``/api/v1/databus/premium/smart-money`` — smart-money wallet feed.
  * ``/api/v1/databus/fetch/{data_type}`` generic fetch for gmgn token
    holders / trades.

If the source returns no enriched lists (or is denied), the tool returns
ok=False — the chat layer must not fabricate buyer names.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.onchain")


def _first_nonempty(*vals: Any) -> Any:
    for v in vals:
        if v:
            return v
    return None


async def top_traders(*, address: str = "", limit: int = 10) -> ToolResult:
    """Top traders / top holders for a token from the backend enrichment."""
    if not address:
        return ToolResult(tool="top_traders", ok=False, error="address required")
    body = await backend_json_get(f"/api/v1/databus/token/{address}", timeout=8.0)
    if not body:
        return ToolResult(tool="top_traders", ok=False, error="no live data (backend token detail unavailable)")

    raw = body.get("data")
    data = raw if isinstance(raw, dict) else body
    top_holders = _first_nonempty(
        data.get("top_holders"),
        data.get("holders"),
        (data.get("gmgn") or {}).get("holders") if isinstance(data.get("gmgn"), dict) else None,
    )
    smart = _first_nonempty(
        data.get("smart_money"),
        (data.get("gmgn") or {}).get("smart_money") if isinstance(data.get("gmgn"), dict) else None,
    )
    traders = _first_nonempty(
        data.get("top_traders"), (data.get("gmgn") or {}).get("traders") if isinstance(data.get("gmgn"), dict) else None
    )

    payload: dict[str, Any] = {}
    if isinstance(top_holders, list):
        payload["top_holders"] = top_holders[:limit]
    if isinstance(smart, list):
        payload["smart_money"] = smart[:limit]
    if isinstance(traders, list):
        payload["top_traders"] = traders[:limit]

    if not payload:
        return ToolResult(
            tool="top_traders",
            ok=False,
            error="no trader/holder data returned for this token",
            addresses=[address],
        )
    # Collect any wallet addresses the source actually returned.
    wallets: list[str] = []
    for bucket in payload.values():
        for item in bucket:
            if isinstance(item, dict):
                addr = item.get("address") or item.get("wallet") or item.get("owner")
                if addr:
                    wallets.append(addr)
    return ToolResult(
        tool="top_traders",
        ok=True,
        data={"address": address, **payload},
        symbols=[],
        addresses=list(dict.fromkeys([address, *wallets])),
        source="backend:/api/v1/databus/token",
    )


async def smart_money(*, chain: str = "solana", limit: int = 10) -> ToolResult:
    """Smart-money wallets / flows from the backend premium feed."""
    body = await backend_json_get("/api/v1/databus/premium/smart-money", params={"chain": chain}, timeout=8.0)
    if not body:
        return ToolResult(tool="smart_money", ok=False, error="no live data (backend smart-money feed unavailable)")

    wallets = body.get("smart_money_wallets")
    if not isinstance(wallets, list) or not wallets:
        # Some payloads nest the list under ``data``.
        inner = body.get("data")
        wallets = inner.get("smart_money_wallets") if isinstance(inner, dict) else None
    if not isinstance(wallets, list) or not wallets:
        return ToolResult(tool="smart_money", ok=False, error="no smart-money wallets returned (feed empty/denied)")

    result = [w for w in wallets[:limit] if isinstance(w, dict)]
    addrs = [w.get("address") for w in result if isinstance(w, dict) and w.get("address")]
    return ToolResult(
        tool="smart_money",
        ok=True,
        data={"chain": chain, "smart_money_wallets": result, "total_tracked": body.get("total_tracked")},
        symbols=[],
        addresses=[a for a in addrs if a],
        source="backend:/api/v1/databus/premium/smart-money",
    )
