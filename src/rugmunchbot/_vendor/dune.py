# VENDORED 2026-08-24 from rmi-backend/app/databus/provider_implementations/dune.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Dune Analytics provider — public query results via Dune API v1 (cached, fail-open).

Curated PUBLIC Dune query IDs, each verified 2026-08-16 against the live API
(key from ``DUNE_API_KEY``, secrets manager). We use the
cached-results endpoint ``GET /api/v1/query/{query_id}/results`` which returns
the LATEST cached rows for a public query — no execution credits consumed.

Honesty contract: rows returned are the real rows Dune served. When a query is
private, expired, failed, or unreachable the provider returns ``None`` for that
query and callers render "—" instead of fabricating numbers. Never invent data.

Verified queries:
  * dex_trades (3426636)      — DEX trades across chains (official docs example)
  * whale_transactions (3493826) — whale-sized DEX transactions (docs filtering example)
  * dai_transfers (3373921)   — DAI transfer history (official quickstart example)
  * dune_api_demo (1215383)   — official dune-client demo (connectivity sanity only)
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger

logger = get_logger(__name__)

DUNE_API_BASE = "https://api.dune.com/api/v1"

# Curated public queries: title -> query_id. Only IDs verified to return real
# cached rows are listed (see module docstring). Unknown titles fail open.
DUNE_QUERIES: dict[str, int] = {
    "dex_trades": 3426636,
    "whale_transactions": 3493826,
    "dai_transfers": 3373921,
    "dune_api_demo": 1215383,
}

_CACHE_TTL = 3600.0  # 1h for successful fetches
_NEGATIVE_TTL = 60.0  # short window for failures so a blip does not poison the hour

# module-level TTL cache: name -> (monotonic_ts, payload)
_CACHE: dict[str, tuple[float, Any]] = {}


def _api_key() -> str:
    """Dune API key from env (seeded from secrets manager)."""
    return os.getenv("DUNE_API_KEY", "").strip()


def _iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _cache_get(name: str) -> Any | None:
    entry = _CACHE.get(name)
    if not entry:
        return None
    ts, payload = entry
    if time.monotonic() - ts < _CACHE_TTL:
        return payload
    _CACHE.pop(name, None)
    return None


def _cache_set(name: str, payload: Any) -> None:
    _CACHE[name] = (time.monotonic(), payload)


def _cache_set_neg(name: str, payload: Any) -> None:
    # Backdate the timestamp so _cache_get (fixed 1h window) expires it after
    # _NEGATIVE_TTL seconds — transient failures don't poison the full hour.
    _CACHE[name] = (time.monotonic() - (_CACHE_TTL - _NEGATIVE_TTL), payload)


def clear_cache() -> None:
    """Drop all cached Dune results (tests / ops)."""
    _CACHE.clear()


async def dune_latest_results(query_id: int, limit: int = 5) -> dict[str, Any] | None:
    """Fetch the LATEST cached rows for a public Dune query.

    Returns a normalized digest with the real rows, or ``None`` when the query
    is private / expired / failed / unreachable. Fail-open, never raises.
    """
    name = next((n for n, q in DUNE_QUERIES.items() if q == query_id), f"query_{query_id}")
    cached = _cache_get(name)
    if cached is not None:
        return cached

    api_key = _api_key()
    if not api_key:
        logger.warning("DUNE_API_KEY not set — dune provider disabled")
        return None

    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(
                f"{DUNE_API_BASE}/query/{query_id}/results",
                params={"limit": max(1, min(int(limit), 50))},
                headers={"X-Dune-API-Key": api_key},
            )
            if r.status_code != 200:
                logger.warning("dune query %s (%s) status=%s", query_id, name, r.status_code)
                _cache_set_neg(name, None)
                return None
            j = r.json()
            state = j.get("state") or ""
            if state not in ("QUERY_STATE_COMPLETED",):
                logger.warning("dune query %s (%s) state=%s", query_id, name, state)
                _cache_set_neg(name, None)
                return None
            result = j.get("result") or {}
            rows = result.get("rows") or []
            meta = result.get("metadata") or {}
            payload = {
                "query_name": name,
                "query_id": query_id,
                "state": state,
                "fetched_at": _iso(),
                "columns": meta.get("column_names", []),
                "rows": rows[:limit],
                "source": "dune",
            }
            _cache_set(name, payload)
            return payload
    except (ValueError, ConnectionError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
        logger.warning("dune query %s (%s) fetch failed", query_id, name)
        _cache_set_neg(name, None)
        return None


async def dune_named(name: str, limit: int = 5) -> dict[str, Any] | None:
    """Fetch a curated Dune query by title. Unknown titles -> None."""
    query_id = DUNE_QUERIES.get(name)
    if not query_id:
        return None
    return await dune_latest_results(query_id, limit=limit)


async def dune_onchain_intel(limit: int = 5) -> dict[str, Any]:
    """Digest across all curated Dune queries — real data only, "—" on failure.

    Each entry is either the normalized result digest or ``{"error": ...}`` so
    callers can render a clean card without fabricating anything.
    """
    limit = max(1, min(int(limit), 10))
    out: dict[str, Any] = {"source": "dune", "queries": {}, "fetched_at": _iso()}
    for name in DUNE_QUERIES:
        try:
            res = await dune_latest_results(DUNE_QUERIES[name], limit=limit)
        except Exception:  # defensive — provider itself never raises, but belt & braces
            logger.warning("dune intel digest error for %s", name)
            res = None
        if res is None:
            out["queries"][name] = {"query_name": name, "query_id": DUNE_QUERIES[name], "error": "unavailable"}
        else:
            out["queries"][name] = res
    return out
