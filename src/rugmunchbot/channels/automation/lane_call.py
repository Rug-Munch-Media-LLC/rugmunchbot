"""Trending / launchpad call feed — the live launch rail, observed now."""

from __future__ import annotations

from datetime import UTC, datetime

from rugmunchbot.channels.content.live_rails import fetch_launches_live
from rugmunchbot.smart_money.types import Feed

#: Fields the call renderer is allowed to print. Anything the source omits
#: stays omitted — a missing liquidity number is never rendered as $0.
_KEEP = (
    "address",
    "symbol",
    "name",
    "chain",
    "liquidity_usd",
    "holders",
    "risk_level",
    "risk_flags",
    "age_hours",
)

_RAIL = "/api/v1/market/launches (live)"


async def fetch_calls(chain: str = "solana", limit: int = 5) -> Feed:
    """Fetch recent launches; empty means an honest empty Feed, never filler.

    ``updated_at`` is the observation time (we fetched now) because the live
    launch rail returns no per-row timestamp. That is stated in the source
    line so a reader can tell observation time from event time.
    """
    rows = await fetch_launches_live(chain, max(1, limit))
    items = tuple(
        {key: row[key] for key in _KEEP if row.get(key) is not None}
        for row in (rows or [])
        if row.get("address") or row.get("symbol")
    )
    if not items:
        return Feed(kind="calls", note="launch feed empty", tried=(_RAIL,))
    return Feed(
        kind="calls",
        items=items,
        source="rmi launch rail (observed at fetch)",
        updated_at=datetime.now(UTC).isoformat(),
        tried=(_RAIL,),
    )
