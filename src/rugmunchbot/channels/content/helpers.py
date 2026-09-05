"""Shared helpers for the channel content fetchers (fail-open by contract)."""

from __future__ import annotations

import asyncio
from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import _float_or_none, _holders_count
from rugmunchbot.formatters._scan_report_riskreport import _build_risk_report_from_scan
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json

logger = get_logger("rmi_bot.channels.content")

_NEWS_WINDOW_HOURS = 168

#: Hard budget for the concurrent DexScreener identity backfill in
#: ``fetch_scan_notes`` (fail-open — a timeout leaves names unresolved).
_IDENTITY_ENRICH_TIMEOUT: float = 6.0

__all__ = [
    "_IDENTITY_ENRICH_TIMEOUT",
    "_NEWS_WINDOW_HOURS",
    "_as_list",
    "_build_risk_report_from_scan",
    "_enrich_missing_identities",
    "_enrich_scan_identity",
    "_float_or_none",
    "_holders_count",
    "_image_url",
    "_is_placeholder",
    "logger",
]


def _image_url(value: Any) -> str:
    """Pass through a usable http(s) image URL from a backend payload ("" else)."""
    if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
        return value.strip()
    return ""


def _is_placeholder(value: object) -> bool:
    """True when a name/symbol field carries no real identity (fail-open)."""
    return str(value or "").strip().lower() in ("", "unknown", "???", "n/a", "null", "none")


async def _enrich_scan_identity(item: dict[str, Any]) -> None:
    """Backfill a scan note's missing name/symbol from DexScreener (in place).

    Only fires when the note carries a placeholder identity (``Unknown`` /
    ``???`` / empty) — real payload names are never overwritten. Uses the
    free DexScreener ``/latest/dex/tokens/{address}`` endpoint via the shared
    budget-gated client (``http_get_json``). Fail-open: a miss, budget skip,
    or error leaves the note untouched; never raises.
    """
    if not (_is_placeholder(item.get("name")) or _is_placeholder(item.get("symbol"))):
        return
    addr = str(item.get("address") or item.get("token_address") or "").strip()
    if not addr:
        return
    try:
        body = await http_get_json(
            f"https://api.dexscreener.com/latest/dex/tokens/{addr}", timeout=5.0
        )
        if not body:
            return
        pairs = body.get("pairs")
        if not isinstance(pairs, list) or not pairs or not isinstance(pairs[0], dict):
            return
        base = pairs[0].get("baseToken")
        if not isinstance(base, dict):
            return
        if _is_placeholder(item.get("name")):
            candidate = str(base.get("name") or "").strip()
            if candidate:
                item["name"] = candidate
        if _is_placeholder(item.get("symbol")):
            candidate = str(base.get("symbol") or "").strip()
            if candidate:
                item["symbol"] = candidate
        if _is_placeholder(item.get("chain")):
            chain = str(pairs[0].get("chainId") or "").strip()
            if chain:
                item["chain"] = chain
    except Exception:
        logger.debug("channels:dex_identity_failed addr=%.40s", addr)


async def _enrich_missing_identities(items: list[dict[str, Any]]) -> None:
    """Concurrent, time-bounded DexScreener identity backfill for scan notes.

    Runs every placeholder-named note's lookup in parallel under a hard
    ``_IDENTITY_ENRICH_TIMEOUT`` so a slow DexScreener never blocks the
    channel tick. Fail-open by contract: any timeout/error leaves the list
    as-is.
    """
    if not items:
        return
    try:
        await asyncio.wait_for(
            asyncio.gather(*(_enrich_scan_identity(it) for it in items), return_exceptions=True),
            timeout=_IDENTITY_ENRICH_TIMEOUT,
        )
    except Exception:
        logger.debug("channels:dex_identity_timeout items=%d", len(items))


def _as_list(value: Any) -> list[dict[str, Any]]:
    """Coerce a JSON value into a list of dicts (fail-open)."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
