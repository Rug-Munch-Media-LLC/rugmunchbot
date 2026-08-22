"""Alert engine price fetching — own scanner cache first, DexScreener fallback.

Extracted verbatim from the former single-module ``handlers/alerts.py``.
"""

from __future__ import annotations

from rugmunchbot.config import DEXSCREENER
from rugmunchbot.data_resolver import backend_get_json, resolve_with_fallback
from rugmunchbot.formatters import detect_chain
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json

logger = get_logger("rmi_bot")


async def _fetch_prices_batch(addresses: list[str]) -> dict[str, float]:
    """Fetch current USD prices for a list of token addresses.

    Own-data first: the backend scanner cache (``/api/v2/scanner/token/price``,
    populated by our own scanners from ClickHouse/DataBus) is the primary
    source; DexScreener is used only as an external fallback for addresses
    the cache does not cover. DATA_SOURCE is logged per batch so reliance
    on the external provider can be tracked.
    """
    prices: dict[str, float] = {}
    if not addresses:
        return prices
    normalized = list({a.lower() for a in addresses})

    async def _own_data() -> dict[str, float]:
        """Primary: scanner price cache for every address (own data)."""
        found: dict[str, float] = {}
        for addr in normalized:
            chain = detect_chain(addr)
            if chain == "unknown":
                continue
            body = await backend_get_json(
                f"/api/v2/scanner/token/price/{chain}/{addr}",
                timeout_secs=4,
            )
            if body and body.get("cached") and body.get("data"):
                price = float(body["data"].get("price_usd") or 0)
                if price > 0:
                    found[addr] = price
        return found

    async def _external() -> dict[str, float]:
        """Fallback: DexScreener batch for any address still missing."""
        missing = [a for a in normalized if a not in prices]
        ext: dict[str, float] = {}
        if not missing:
            return ext
        batch = ",".join(missing)
        data = await http_get_json(f"{DEXSCREENER}/tokens/{batch}", timeout=10)
        if data is None:
            logger.warning("Batch price fetch error")
            return ext
        pairs = data.get("pairs", [])
        for pair in pairs:
            addr = pair.get("baseToken", {}).get("address", "").lower()
            price = float(pair.get("priceUsd", 0))
            if addr and price > 0 and addr not in ext:
                ext[addr] = price
        return ext

    # Own data first: try the scanner cache for the whole batch. If ANY address
    # resolves from own data, keep those hits and only fill the rest externally
    # (the resolver logs DATA_SOURCE=own_data when at least one hit came from
    # our cache). If the cache is completely cold, the resolver falls back to
    # DexScreener for the whole batch and logs DATA_SOURCE=fallback_external.
    prices = await resolve_with_fallback(
        "alert_price_batch",
        _own_data,
        _external,
        timeout_secs=8,
        is_valid=lambda d: bool(d),
    )
    if len(prices) < len(normalized):
        ext = await _external()
        prices.update(ext)
    return prices
