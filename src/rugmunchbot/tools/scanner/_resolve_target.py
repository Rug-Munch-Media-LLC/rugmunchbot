"""Scan-target resolution — tickers ($SYMBOL, plain text) → contract addresses.

Root cause of the "Unknown (???) / Score 41 / every metric empty" scans
(2026-08-25): ``cmd_scan`` passed ``$smolcat`` straight into the scanner as
if it were an address. Every provider missed, yet the report still rendered
a verdict — garbage in, confident garbage out.

This resolver makes every scan target REAL before the scanner runs:

* address-shaped input passes through untouched (per-chain shape check),
* ``$TICKER`` / plain tickers resolve through the DexScreener search API —
  the same live source the bot's own market answers cite — picking the
  symbol-matching pair with the deepest liquidity (what the user means),
* the winning pair's ``baseToken.address`` + ``chainId`` feed the scanner,
  and the full pair payload rides along so the report can render real
  market data even when the v2 backend has nothing.

Fail-closed at the entry point: an unresolvable non-address target returns
``None`` so the handler shows "token not found" instead of scanning junk.
"""

from __future__ import annotations

from typing import Any

import httpx

from rugmunchbot.config import DEXSCREENER
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json
from rugmunchbot.tools.scanner._providers import _detect_chain

logger = get_logger("rmi_bot.tools.scanner")

__all__ = ["ResolvedTarget", "resolve_scan_target"]


class ResolvedTarget:
    """A scan-ready target: real address + chain (+ optional pair payload)."""

    __slots__ = ("address", "chain", "pair", "query", "resolved_from")

    def __init__(
        self,
        address: str,
        chain: str,
        *,
        query: str = "",
        pair: dict[str, Any] | None = None,
        resolved_from: str = "address",
    ) -> None:
        self.address = address
        self.chain = chain
        self.query = query
        self.pair = pair
        self.resolved_from = resolved_from


def _strip_ticker(target: str) -> str:
    """'$smolcat' → 'SMOLCAT'; keeps real addresses untouched."""
    t = (target or "").strip()
    if t.startswith("$"):
        t = t[1:]
    return t.strip()


def _pair_liquidity(pair: dict[str, Any]) -> float:
    liq = pair.get("liquidity")
    if isinstance(liq, dict):
        try:
            return float(liq.get("usd") or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _pick_best_pair(pairs: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    """Symbol-matching pair with the deepest liquidity (fallback: pairs[0])."""
    want = symbol.strip().lower()
    matching = [
        p
        for p in pairs
        if isinstance(p.get("baseToken"), dict)
        and str((p["baseToken"] or {}).get("symbol") or "").strip().lower() == want
        and str((p["baseToken"] or {}).get("address") or "").strip()
    ]
    pool = matching or pairs
    if not pool:
        return None
    return max(pool, key=_pair_liquidity)


async def resolve_scan_target(target: str, chain: str | None = "") -> ResolvedTarget | None:
    """Resolve any /scan target to a real (address, chain).

    Address-shaped input short-circuits (zero added latency). Anything else
    goes through DexScreener search; the best symbol-matching pair wins.
    Returns ``None`` when a non-address target resolves to nothing — callers
    must show "not found" rather than scan a junk string. Never raises.
    """
    raw = (target or "").strip()
    if not raw:
        return None

    # 1. Already an address → pass through with chain detection.
    detected = _detect_chain(raw)
    if detected and detected != "unknown":
        return ResolvedTarget(address=raw, chain=(chain or detected).strip())

    # 2. Ticker / free-text → DexScreener search.
    symbol = _strip_ticker(raw)
    if not symbol:
        return None
    try:
        body = await http_get_json(f"{DEXSCREENER}/search", params={"q": symbol}, timeout=8.0)
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        logger.debug("scan_target_search_failed q=%s", symbol[:24])
        return None
    pairs = (body or {}).get("pairs") if isinstance(body, dict) else None
    if not isinstance(pairs, list) or not pairs:
        return None
    best = _pick_best_pair(pairs, symbol)
    if best is None:
        return None
    base = best.get("baseToken") or {}
    address = str(base.get("address") or "").strip()
    if not address:
        return None
    resolved_chain = str(chain or "").strip() or str(best.get("chainId") or "").strip()
    logger.info(
        "scan_target_resolved q=%s -> %s chain=%s liq=%.0f from=search",
        symbol[:24],
        address[:16],
        resolved_chain,
        _pair_liquidity(best),
    )
    return ResolvedTarget(
        address=address,
        chain=resolved_chain,
        query=symbol,
        pair=best,
        resolved_from="dexscreener_search",
    )
