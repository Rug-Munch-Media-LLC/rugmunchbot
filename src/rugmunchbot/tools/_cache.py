"""Short-TTL in-memory cache for the bot tool registry.

Every tool result is cached for a short TTL (default 60-120s) so repeated
questions ("what's the price of X" twice in a row) hit the cache instead of
re-hitting external APIs, while keeping data fresh enough for market claims.
Cached results are still REAL API data — they are never synthesized.

Stable-data TTLs: ``tools/budget.py`` carries the ``TTL_POLICY`` table
(prices 60s, trending 300s, market-overview 600s, ...) which is applied on
every ``set`` for a known tool namespace — longer-lived data means fewer
API calls and less free-credit burn.

Cache keys are stable and cheap: the registry builds a normalized logical
key from the tool name + kwargs, and :func:`make_cache_key` hashes it into a
fixed-length digest so the in-memory store stays compact even for verbose
kwargs (requirement: stable across runs + cheap to compare).

Thread-safety: the bot runs a single asyncio event loop, and every access
happens on that loop (tools are async), so a plain dict is sufficient.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Generic, TypeVar

from rugmunchbot.tools.budget import effective_ttl

T = TypeVar("T")


def make_cache_key(namespace: str, key: str) -> str:
    """Stable, cheap storage key: ``sha256(namespace|key)[:32]``.

    Deterministic across runs (identical query + provider → identical key) and
    bounded in size regardless of how verbose the logical key is.
    """
    digest = hashlib.sha256(f"{namespace}\u0000{key}".encode()).hexdigest()
    return digest[:32]


class TTLCache(Generic[T]):
    """Minimal TTL cache keyed by ``(namespace, key)``.

    The stored key is the stable hash of ``(namespace, key)`` (see
    :func:`make_cache_key`); the namespace stays readable so
    :meth:`clear` can evict whole namespaces.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], tuple[float, T]] = {}

    def _skey(self, namespace: str, key: str) -> str:
        return make_cache_key(namespace, key)

    def get(self, namespace: str, key: str) -> T | None:
        skey = self._skey(namespace, key)
        item = self._store.get((namespace, skey))
        if item is None:
            return None
        expires_at, value = item
        if time.monotonic() > expires_at:
            self._store.pop((namespace, skey), None)
            return None
        return value

    def set(self, namespace: str, key: str, value: T, ttl: float) -> None:
        # Stable-data TTL policy wins for known tool namespaces (longer-lived
        # data = fewer API calls); unknown namespaces keep the caller TTL.
        effective = effective_ttl(namespace, ttl)
        self._store[(namespace, self._skey(namespace, key))] = (time.monotonic() + effective, value)

    def clear(self, namespace: str | None = None) -> None:
        if namespace is None:
            self._store.clear()
            return
        for k in [k for k in self._store if k[0] == namespace]:
            self._store.pop(k, None)

    def __len__(self) -> int:
        return len(self._store)

    def _snapshot(self) -> dict[tuple[str, str], Any]:
        """Test helper: raw store contents (expired entries included)."""
        return dict(self._store)
