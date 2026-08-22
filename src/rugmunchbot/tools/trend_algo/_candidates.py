"""trend_algo candidate collection — own data first, DexScreener seeds last.

Extracted verbatim from the former single-module ``tools/trend_algo.py``.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import BackendApiError, http_get_any
from rugmunchbot.tools.registry import backend_json_get, backend_json_get_any
from rugmunchbot.tools.trend_algo._constants import (
    _LAUNCHES_LIMIT,
    _SCANNER_RECENT_LIMIT,
    _TRENDING_LIMIT,
    DEXSCREENER_TOKEN_PROFILES,
)
from rugmunchbot.tools.trend_algo._normalize import _normalize_chain, _to_float

logger = get_logger("rmi_bot.tools.trend_algo")


async def _scanner_candidates() -> list[dict[str, Any]]:
    """OUR scanner activity: real risk scores + honeypot flags per token."""
    try:
        # ``/api/v1/scanner/recent`` answers with a JSON ARRAY — the dict-only
        # loud helper rejects arrays, so use the list-capable loud variant.
        body = await backend_json_get_any(
            "/api/v1/scanner/recent", params={"limit": _SCANNER_RECENT_LIMIT}, timeout=8.0
        )
    except BackendApiError as exc:
        logger.debug("trend_algo scanner backend miss (%s): %s", exc.status_code, exc)
        return []
    if not isinstance(body, list):
        return []
    out: list[dict[str, Any]] = []
    for it in body:
        if not isinstance(it, dict):
            continue
        addr = it.get("token_address") or it.get("contract_address") or ""
        if not addr:
            continue
        chain = _normalize_chain(str(it.get("chain") or ""))
        out.append(
            {
                "address": addr,
                "chain": chain,
                "symbol": (it.get("token_symbol") or "").strip(),
                "risk_score": _to_float(it.get("risk_score")),
                "is_honeypot": bool(it.get("is_honeypot")),
                "is_rug_pull": bool(it.get("is_rug_pull")),
                "scanned_at": it.get("scanned_at"),
                "source": "own:scanner/recent",
            }
        )
    return out


async def _trending_candidates() -> list[dict[str, Any]]:
    """Our fused trending feed — real symbols/addresses/chain."""
    try:
        body = await backend_json_get(
            "/api/v1/market/trending", params={"limit": _TRENDING_LIMIT}, timeout=8.0
        )
    except BackendApiError as exc:
        # One candidate source among several (gathered WITHOUT return_exceptions
        # in trend_by_chain) — a dead backend must degrade THIS leg to empty,
        # not crash the whole candidate gather.
        logger.debug("trend_algo trending backend miss (%s): %s", exc.status_code, exc)
        return []
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        addr = it.get("address") or it.get("token_address") or ""
        if not addr:
            continue
        chain = _normalize_chain(str(it.get("chain") or ""))
        if chain == "all":
            continue
        out.append(
            {
                "address": addr,
                "chain": chain,
                "symbol": (it.get("symbol") or "").strip(),
                "source": "own:market/trending",
            }
        )
    return out


async def _launch_candidates() -> list[dict[str, Any]]:
    """ClickHouse deployer_events via our launch feed (own indexer data)."""
    try:
        body = await backend_json_get(
            "/api/v1/launches/recent", params={"limit": _LAUNCHES_LIMIT}, timeout=8.0
        )
    except BackendApiError as exc:
        # Same rationale as _trending_candidates: degrade this leg to empty.
        logger.debug("trend_algo launches backend miss (%s): %s", exc.status_code, exc)
        return []
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # Only our own ClickHouse rows — DexScreener boost rows carry no
        # symbol and no launch timestamp, so they add no scoring signal.
        if it.get("source") != "own_clickhouse":
            continue
        addr = it.get("token_address") or ""
        if not addr:
            continue
        chain = _normalize_chain(str(it.get("chain") or ""))
        out.append(
            {
                "address": addr,
                "chain": chain,
                "symbol": (it.get("symbol") or "").strip(),
                "launch_timestamp": it.get("launch_timestamp"),
                "source": "own:clickhouse:deployer_events",
            }
        )
    return out


async def _dex_profile_candidates() -> list[dict[str, Any]]:
    """DexScreener fresh-launch profiles (real addresses + chain seeds)."""
    body = await http_get_any(DEXSCREENER_TOKEN_PROFILES, timeout=8.0)
    if not isinstance(body, list):
        return []
    out: list[dict[str, Any]] = []
    for it in body:
        if not isinstance(it, dict):
            continue
        addr = it.get("tokenAddress") or ""
        if not addr:
            continue
        chain = _normalize_chain(str(it.get("chainId") or ""))
        out.append(
            {"address": addr, "chain": chain, "symbol": "", "source": "dexscreener:token-profiles"}
        )
    return out
