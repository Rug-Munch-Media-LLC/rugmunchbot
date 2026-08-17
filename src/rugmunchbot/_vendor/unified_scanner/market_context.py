# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/market_context.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Market context fetch + cache for the unified scanner.

Fetches the market conditions that influence token risk from RugBrain
(rmi-rag) before each scan:

  * ``GET /api/v1/fear-index``      — crypto panic/fear index (0-100)
  * ``GET /api/v1/market/mood``     — market mood + recent exploit count +
                                      chain activity digest

Results are cached in Redis (key ``scanner:market_context:{chain}``,
TTL ``MARKET_CONTEXT_CACHE_TTL`` = 300s) so a scan storm does not hammer
RugBrain. Cache is per-chain because the global fear index and exploit
count are shared by every token on that chain; the token-specific social
velocity is fetched separately (and cached under the token address).

Fail-open: any RugBrain outage yields a neutral ``MarketContext`` with
``source="fallback"`` so the scan never fails because market context is
unavailable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import redis_get, redis_set
from rugmunchbot._vendor.unified_scanner.model import (
    MARKET_CONTEXT_CACHE_TTL,
    RUG_BRAIN_API_URL,
    MarketContext,
)

logger = get_logger(__name__)

_FEAR_INDEX_URL = f"{RUG_BRAIN_API_URL}/api/v1/fear-index"
_MARKET_MOOD_URL = f"{RUG_BRAIN_API_URL}/api/v1/market/mood"
_MARKET_CONTEXT_URL = f"{RUG_BRAIN_API_URL}/api/v1/market/context"

_HTTP_TIMEOUT = 5.0


def _cache_key(chain: str) -> str:
    return f"scanner:market_context:{chain}"


def _social_cache_key(chain: str, address: str) -> str:
    return f"scanner:market_context:{chain}:{address[:24]}"


def _neutral(reason: str = "unavailable") -> MarketContext:
    return MarketContext(
        source=f"fallback:{reason}",
        fetched_at=datetime.now(UTC).isoformat(),
    )


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """GET a JSON payload, returning None on any failure."""
    try:
        r = await client.get(url, params=params, timeout=_HTTP_TIMEOUT)
        if r.status_code != 200:
            logger.debug("market_context_http_failed", url=url, status=r.status_code)
            return None
        data = r.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, OSError, ValueError, TimeoutError) as exc:
        logger.debug("market_context_fetch_failed", url=url, error=str(exc)[:120])
        return None


async def _fetch_global_mood(client: httpx.AsyncClient) -> dict[str, Any]:
    """Fetch + merge the two RugBrain global endpoints into a mood dict."""
    fear_data = await _get_json(client, _FEAR_INDEX_URL, {"window": "24h"})
    mood_data = await _get_json(client, _MARKET_MOOD_URL)

    mood: dict[str, Any] = {"source": "rugbrain"}
    if isinstance(fear_data, dict):
        mood["fear_index"] = float(fear_data.get("value", 50) or 50)
    if isinstance(mood_data, dict):
        mood["recent_exploits"] = int(mood_data.get("recent_exploits_24h", 0) or 0)
        mood["chain_volume_24h"] = float(mood_data.get("chain_volume_24h", 0) or 0)
        mood["market_sentiment"] = mood_data.get("market_sentiment", "neutral")
        # If the mood endpoint already includes chain-level activity, keep it.
        if mood_data.get("chain_volume_24h") is None and mood_data.get("chain_activity"):
            activity = mood_data.get("chain_activity") or {}
            mood["chain_volume_24h"] = float(activity.get("tvl_change_24h", 0) or 0)
    return mood


async def _fetch_token_social(client: httpx.AsyncClient, address: str, chain: str) -> dict[str, Any]:
    """Fetch token-specific social signals from RugBrain market/context."""
    data = await _get_json(client, f"{_MARKET_CONTEXT_URL}/{address}", {"chain": chain})
    if not isinstance(data, dict):
        return {}
    social = data.get("social_signals") or {}
    return {
        "social_velocity": float(social.get("mention_velocity", 0) or 0),
        "deployer_freshness": str(data.get("deployer_freshness", "unknown") or "unknown"),
        "similar_tokens": data.get("similar_tokens") or [],
        "recent_exploits_same_category": data.get("recent_exploits") or [],
    }


async def fetch_market_context(address: str, chain: str) -> MarketContext:
    """Build the market context for *address* on *chain* (cached 5 min).

    Returns a neutral ``MarketContext`` (source ``fallback:*``) when
    RugBrain is unreachable — market context is best-effort enrichment,
    never a scan blocker.
    """
    try:
        # ── Global mood: cached per chain ──
        key = _cache_key(chain)
        cached = await redis_get(key)
        if isinstance(cached, dict):
            mood: dict[str, Any] = cached
        else:
            async with httpx.AsyncClient() as client:
                mood = await _fetch_global_mood(client)
            if mood.get("fear_index") is None and mood.get("recent_exploits") is None:
                # Neither global endpoint responded — do not cache a broken snapshot.
                return _neutral("rugbrain_unavailable")
            await redis_set(key, mood, ttl=MARKET_CONTEXT_CACHE_TTL)

        # ── Token-specific social signals: cached per token ──
        social_key = _social_cache_key(chain, address)
        social_cached = await redis_get(social_key)
        if isinstance(social_cached, dict):
            token_signals: dict[str, Any] = social_cached
        else:
            async with httpx.AsyncClient() as client:
                token_signals = await _fetch_token_social(client, address, chain)
            if token_signals:
                await redis_set(social_key, token_signals, ttl=MARKET_CONTEXT_CACHE_TTL)

        ctx = MarketContext(
            fear_index=float(mood.get("fear_index", 50) or 50),
            recent_exploits=int(mood.get("recent_exploits", 0) or 0),
            chain_volume_24h=float(mood.get("chain_volume_24h", 0) or 0),
            social_velocity=float(token_signals.get("social_velocity", 0) or 0),
            deployer_freshness=str(token_signals.get("deployer_freshness", "unknown") or "unknown"),
            source="rugbrain",
            fetched_at=datetime.now(UTC).isoformat(),
        )
        return ctx
    except (httpx.HTTPError, OSError, ValueError, TimeoutError) as exc:
        logger.debug("market_context_unavailable", error=str(exc)[:120])
        return _neutral("error")


__all__ = [
    "MarketContext",
    "fetch_market_context",
]
