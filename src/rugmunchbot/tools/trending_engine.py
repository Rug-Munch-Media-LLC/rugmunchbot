"""Canonical trending engine client — ``rugbrain.trending_fusion/v1``.

The bot never scores a token. It reads the ranked list the engine publishes at
``GET {RUGBRAIN_API}/api/v1/trending`` and passes the payload (``ranked``,
``paid``, ``disclosures``, ``stale``, ``missing_sources``) through to the
renderer. Engine down -> :class:`TrendingEngineUnavailable`; callers render
``unavailable (source)``. A local fallback scorer is forbidden:
``standards/fleet/TRENDING-CONTRACT.md``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

ENGINE_ID = "rugbrain.trending_fusion/v1"
UNAVAILABLE_SOURCE = "rugbrain.trending_fusion"
DEFAULT_BASE = "http://127.0.0.1:8000"


class TrendingEngineUnavailable(RuntimeError):
    """The canonical engine did not serve a valid ranked payload."""

    def __init__(self, reason: str, source: str = UNAVAILABLE_SOURCE) -> None:
        """Record the typed reason so the renderer can show ``unavailable (source)``."""
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


def engine_base() -> str:
    """Base URL of the canonical engine (env ``RUGBRAIN_API``)."""
    return (os.environ.get("RUGBRAIN_API") or DEFAULT_BASE).rstrip("/")


async def fetch_trending(chain: str = "solana", limit: int = 20) -> dict[str, Any]:
    """Fetch the canonical ranked payload; raise on outage, bad shape or wrong engine."""
    url = f"{engine_base()}/api/v1/trending"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params={"chain": chain, "limit": limit})
    except Exception as exc:  # transport failure is an outage, not an empty list
        raise TrendingEngineUnavailable(f"transport:{type(exc).__name__}") from exc
    if resp.status_code != 200:
        raise TrendingEngineUnavailable(f"http:{resp.status_code}")
    body = resp.json()
    if not isinstance(body, dict):
        raise TrendingEngineUnavailable("malformed:not_object")
    if body.get("status") == "unavailable":
        raise TrendingEngineUnavailable(str(body.get("reason") or "engine_reported_unavailable"))
    if body.get("engine") != ENGINE_ID:
        raise TrendingEngineUnavailable(f"wrong_engine:{body.get('engine')}")
    return body


def ranked_tokens(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten the engine payload into renderer token dicts, flags intact."""
    meta = {
        "engine": payload.get("engine"),
        "generated_at": payload.get("generated_at"),
        "stale": bool(payload.get("stale")),
        "missing_sources": list(payload.get("missing_sources") or []),
        "paid_only_excluded": list(payload.get("paid_only_excluded") or []),
    }
    tokens: list[dict[str, Any]] = []
    for row in payload.get("ranked") or []:
        if not isinstance(row, dict):
            continue
        token = dict(row)
        token["address"] = row.get("token_address") or row.get("address")
        token["source"] = "rugbrain"
        token["engine_meta"] = meta
        tokens.append(token)
    return tokens


def paid_summary(payload: dict[str, Any]) -> str | None:
    """Footer line: "N of M are paid placements" (None when nothing is paid)."""
    ranked = [r for r in (payload.get("ranked") or []) if isinstance(r, dict)]
    paid = sum(1 for r in ranked if r.get("paid") or r.get("paid_only"))
    if not paid:
        return None
    return f"{paid} of {len(ranked)} are paid placements"
