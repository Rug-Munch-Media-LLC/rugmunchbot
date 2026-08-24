# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/base.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Shared scanner engine base class — ClickHouse client, chain normalization, cache, results.

Consolidates the lifecycle that every ClickHouse-backed scanner engine repeats:

  1. **ClickHouse client** — lazy, concurrency-safe client for ``wallet_memory``.
     Delegates to the canonical ``lite_chain_indexer._get_ch_client`` so the whole
     process shares ONE client (the chain-indexer version builds a local
     candidate and only publishes it after ``SELECT 1`` succeeds, avoiding the
     half-initialized-client race that engine-local copies had).
  2. **Chain normalization** — delegates to ``lite_chain_indexer._normalize_chain``
     so engine-local ``_CHAIN_MAP`` copies are not maintained in N places.
  3. **In-process TTL cache** — standard get/put/stats/clear semantics with
     per-engine TTL and max-size knobs (mirrors the old module-level
     ``_CACHE`` + ``_CACHE_TTL`` + ``_CACHE_MAX`` convention).
  4. **Result builders** — ``unavailable_result`` / ``error_result`` shapes.

Usage
-----
Engines keep their function-based public API (``get_own_volume_authenticity``
etc.) and delegate plumbing through a module-level engine instance::

    class _VolumeEngine(BaseEngine):
        check_name = "volume_authenticity"
        cache_ttl = 600
        cache_max = 100

    _engine = _VolumeEngine()

    # Keep module-level aliases so existing call sites + test patch targets
    # (``_get_ch_client``, ``_normalize_chain``) keep working unchanged:
    _get_ch_client = _engine.client
    _normalize_chain = _engine.normalize_chain

Migration notes
---------------
- ``deployer_fingerprint`` / ``funding_analysis`` already import the canonical
  client from ``lite_chain_indexer`` and are NOT converted to this base: their unit
  tests patch module-level ``_get_ch_client`` / ``_redis_get`` / ``_redis_set``
  names, and the modules carry explicit deferred-split markers.
- Cache and result helpers here are thin wrappers around ``engines._common``,
  which remains the home for the shared async Redis / run_sync helpers.
"""

from __future__ import annotations

import time
from typing import Any

from rugmunchbot._vendor.scanner_engines import _common

# Default cache parameters (mirrors the old per-engine defaults).
_DEFAULT_CACHE_TTL = 600  # 10 minutes
_DEFAULT_CACHE_MAX = 200


class BaseEngine:
    """Shared lifecycle for ClickHouse-backed scanner engines.

    Subclasses set ``check_name`` / ``cache_ttl`` / ``cache_max`` and expose a
    module-level instance; the module's public functions call the instance
    methods for client access, chain normalization, caching, and result
    building.
    """

    #: Short identifier used in logs / stats (e.g. ``"volume_authenticity"``).
    check_name: str = "engine"
    #: In-process cache TTL (seconds).
    cache_ttl: int = _DEFAULT_CACHE_TTL
    #: In-process cache max entries (LRU-style eviction of oldest).
    cache_max: int = _DEFAULT_CACHE_MAX

    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        # Mirror of the canonical client (None until first successful connect).
        self._client: Any = None

    # ── ClickHouse client ──────────────────────────────────────────────────

    def client(self) -> Any | None:
        """Return the canonical lazy ClickHouse client (lite_chain_indexer singleton).

        Caches the result on the instance so ``get_cache_stats`` can report
        connectivity without re-probing.
        """
        from rugmunchbot._vendor.lite_chain_indexer import _get_ch_client

        self._client = _get_ch_client()
        return self._client

    # ── Chain normalization ────────────────────────────────────────────────

    def normalize_chain(self, chain: str) -> str:
        """Normalize a chain name to ClickHouse format (canonical map)."""
        from rugmunchbot._vendor.lite_chain_indexer import _normalize_chain

        return _normalize_chain(chain)

    # ── Cache ──────────────────────────────────────────────────────────────

    def cache_get(self, key: str, *, force_fresh: bool = False) -> dict[str, Any] | None:
        """Return a cached payload (public fields only) or ``None``.

        Honors ``cache_ttl``; when ``force_fresh`` is set the cache is bypassed.
        """
        if force_fresh:
            return None
        cached = self._cache.get(key)
        if cached is None:
            return None
        if time.time() - cached.get("_cached_at", 0) >= self.cache_ttl:
            return None
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    def cache_put(self, key: str, data: dict[str, Any]) -> None:
        """Store a payload with a ``_cached_at`` timestamp, evicting oldest."""
        data["_cached_at"] = time.time()
        self._cache[key] = data
        if len(self._cache) > self.cache_max:
            self._cache.pop(next(iter(self._cache)), None)

    def cache_stats(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        """Standard cache statistics for monitoring."""
        stats = _common.get_cache_stats(
            self._cache,
            self.cache_max,
            self.cache_ttl,
            extra=extra or {"clickhouse_connected": self._client is not None},
        )
        return stats

    def clear(self) -> None:
        """Clear the in-process cache (for testing / memory pressure)."""
        _common.clear_cache(self._cache)

    # ── Result builders ────────────────────────────────────────────────────

    @staticmethod
    def unavailable_result(reason: str = "data_unavailable") -> dict[str, Any]:
        """Standard unavailable result shape."""
        return _common.unavailable_result(reason)

    @staticmethod
    def error_result(error: str) -> dict[str, Any]:
        """Standard error result shape."""
        return _common.error_result(error)
