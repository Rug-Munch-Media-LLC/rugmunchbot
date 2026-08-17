# VENDORED 2026-08-17 from rmi-backend/app/core/degradation.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Graceful Degradation System — RMI Backend
==========================================
When data stores go down, never return 500. Degrade gracefully instead.

Usage:
    # Decorator pattern
    @degradable(stores=["clickhouse"], fallback=[])
    async def get_labels(address: str): ...

    # Context manager
    async with degradation_context("clickhouse") as chain:
        result = await chain.try_primary(lambda: query_clickhouse(...))

    # Direct chain
    chain = DegradationChain("clickhouse")
    result = await chain.execute(lambda: clickhouse_query(), lambda: duckdb_fallback())

Architecture:
    Each store type has a pre-configured chain:
      clickhouse: ClickHouse(3s) -> DuckDB(2s) -> empty[]
      neo4j:      Neo4j(2s) -> Postgres(2s) -> empty[]
      redis:      Redis(1s) -> InProcessLRU -> degraded
      minio:      MinIO(3s) -> cached_list -> file_not_found
      postgres:   Postgres(3s) -> DuckDB(2s) -> empty[]

Response headers:
    X-Degraded-Stores: clickhouse,neo4j    (which stores are in degraded state)
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

from rugmunchbot._vendor.logging_shim import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

T = TypeVar("T")

# ══════════════════════════════════════════════════════════════════════
# TYPES
# ══════════════════════════════════════════════════════════════════════


class StoreHealth(Enum):
    """Current health state of a store."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


class DegradationTier(Enum):
    """Tier in the degradation chain."""

    PRIMARY = 1
    SECONDARY = 2
    TERTIARY = 3
    CACHE = 4
    EMPTY = 5


@dataclass
class StoreConfig:
    """Configuration for a store in the degradation chain."""

    name: str
    tier: DegradationTier
    timeout_seconds: float = 3.0
    max_consecutive_failures: int = 3
    stale_cache_ttl: int = 300  # seconds to serve stale Redis cache

    # Runtime state
    health: StoreHealth = StoreHealth.UNKNOWN
    consecutive_failures: int = 0
    total_successes: int = 0
    total_failures: int = 0
    last_failure_time: float = 0
    last_success_time: float = 0

    @property
    def is_available(self) -> bool:
        return self.health != StoreHealth.DOWN


@dataclass
class DegradationResult:
    """Result of a degradation chain execution."""

    data: Any
    store_used: str
    tier_used: DegradationTier
    degraded: bool = False
    stores_tried: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    latency_ms: float = 0

    @property
    def ok(self) -> bool:
        return self.store_used not in ("empty", "degraded")


# ══════════════════════════════════════════════════════════════════════
# IN-PROCESS LRU CACHE (Redis fallback)
# ══════════════════════════════════════════════════════════════════════


class InProcessLRU:
    """Thread-safe in-process LRU cache for Redis degradation.

    Used when Redis is down: keeps a bounded cache in application memory
    so frequently-accessed data (auth sessions, price quotes, wallet labels)
    remains available.
    """

    def __init__(self, maxsize: int = 1024, ttl_seconds: int = 300) -> None:
        self._cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._maxsize = maxsize
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            expires_at, value = self._cache[key]
            if time.monotonic() > expires_at:
                del self._cache[key]
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl = ttl or self._ttl_seconds
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self._maxsize:
                self._cache.popitem(last=False)
                self._evictions += 1
            self._cache[key] = (time.monotonic() + ttl, value)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "size": len(self._cache),
                "maxsize": self._maxsize,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
            }


# ══════════════════════════════════════════════════════════════════════
# DEGRADATION REGISTRY (global state, X-Degraded-Stores header)
# ══════════════════════════════════════════════════════════════════════


class DegradationRegistry:
    """Global registry tracking which stores are currently degraded.

    Used to:
      - Set X-Degraded-Stores response header
      - Drive /health endpoint awareness
      - Cooldown degraded stores (auto-recover after N seconds)
    """

    def __init__(self) -> None:
        self._stores: dict[str, StoreConfig] = {}
        self._cooldown_seconds: float = 30.0  # auto-recheck after 30s
        self._in_process_lru = InProcessLRU(maxsize=2048, ttl_seconds=300)

    def register(self, store_name: str, config: StoreConfig) -> None:
        self._stores[store_name] = config

    def get(self, store_name: str) -> StoreConfig | None:
        return self._stores.get(store_name)

    def mark_healthy(self, store_name: str) -> None:
        store = self._stores.get(store_name)
        if store:
            store.health = StoreHealth.HEALTHY
            store.consecutive_failures = 0
            store.last_success_time = time.monotonic()

    def mark_degraded(self, store_name: str) -> None:
        store = self._stores.get(store_name)
        if store:
            was_healthy = store.health == StoreHealth.HEALTHY
            store.health = StoreHealth.DEGRADED
            store.last_failure_time = time.monotonic()
            if was_healthy:
                logger.warning(f"Store degraded: {store_name} -> DEGRADED")

    def mark_down(self, store_name: str) -> None:
        store = self._stores.get(store_name)
        if store:
            was_up = store.health in (StoreHealth.HEALTHY, StoreHealth.DEGRADED)
            store.health = StoreHealth.DOWN
            store.consecutive_failures += 1
            store.total_failures += 1
            store.last_failure_time = time.monotonic()
            if was_up:
                logger.error(f"Store down: {store_name} -> DOWN ({store.consecutive_failures} consecutive)")

    def record_success(self, store_name: str) -> None:
        store = self._stores.get(store_name)
        if store:
            store.total_successes += 1
            if store.health == StoreHealth.DOWN and time.monotonic() - store.last_failure_time > self._cooldown_seconds:
                self.mark_healthy(store_name)
                logger.info(f"Store recovered: {store_name} -> HEALTHY")

    def should_attempt(self, store_name: str) -> bool:
        """Check if store should even be attempted based on health/cooldown."""
        store = self._stores.get(store_name)
        if not store:
            return True
        if store.health == StoreHealth.HEALTHY or store.health == StoreHealth.UNKNOWN:
            return True
        if store.health == StoreHealth.DEGRADED:
            return True
        # DOWN: attempt after cooldown
        if store.health == StoreHealth.DOWN:
            return time.monotonic() - store.last_failure_time > self._cooldown_seconds
        return True

    @property
    def degraded_stores(self) -> list[str]:
        """Stores currently in degraded or down state (for X-Degraded-Stores header)."""
        return sorted(name for name, s in self._stores.items() if s.health in (StoreHealth.DEGRADED, StoreHealth.DOWN))

    @property
    def degraded_header(self) -> str:
        """Comma-separated degraded store names for HTTP header."""
        names = self.degraded_stores
        return ",".join(names) if names else ""

    @property
    def lru(self) -> InProcessLRU:
        return self._in_process_lru

    def health_report(self) -> dict[str, Any]:
        return {
            name: {
                "health": s.health.value,
                "tier": s.tier.name,
                "consecutive_failures": s.consecutive_failures,
                "total_successes": s.total_successes,
                "total_failures": s.total_failures,
            }
            for name, s in self._stores.items()
        }


_registry: DegradationRegistry | None = None


def get_registry() -> DegradationRegistry:
    global _registry
    if _registry is None:
        _registry = DegradationRegistry()
        _init_default_stores(_registry)
    return _registry


def _init_default_stores(registry: DegradationRegistry) -> None:
    """Register the default store configurations."""
    defaults = {
        "clickhouse": StoreConfig(
            name="clickhouse",
            tier=DegradationTier.PRIMARY,
            timeout_seconds=3.0,
            max_consecutive_failures=3,
        ),
        "duckdb": StoreConfig(
            name="duckdb",
            tier=DegradationTier.SECONDARY,
            timeout_seconds=2.0,
            max_consecutive_failures=5,
        ),
        "neo4j": StoreConfig(
            name="neo4j",
            tier=DegradationTier.PRIMARY,
            timeout_seconds=2.0,
            max_consecutive_failures=3,
        ),
        "postgres": StoreConfig(
            name="postgres",
            tier=DegradationTier.PRIMARY,
            timeout_seconds=3.0,
            max_consecutive_failures=3,
        ),
        "redis": StoreConfig(
            name="redis",
            tier=DegradationTier.PRIMARY,
            timeout_seconds=1.0,
            max_consecutive_failures=3,
            stale_cache_ttl=300,
        ),
        "minio": StoreConfig(
            name="minio",
            tier=DegradationTier.PRIMARY,
            timeout_seconds=3.0,
            max_consecutive_failures=3,
        ),
    }
    for name, cfg in defaults.items():
        registry.register(name, cfg)


# ══════════════════════════════════════════════════════════════════════
# DEGRADATION CHAIN ENGINE
# ══════════════════════════════════════════════════════════════════════


class DegradationChain:
    """Core degradation chain: try stores in order with timeouts.

    Usage:
        chain = DegradationChain("clickhouse")
        result = await chain.execute(
            primary=lambda: clickhouse_query(),
            secondary=lambda: duckdb_query(),
            fallback=[],
        )
    """

    def __init__(self, store_name: str) -> None:
        self._store_name = store_name
        self._registry = get_registry()
        self._store = self._registry.get(store_name)

    async def execute(
        self,
        primary: Callable[[], Any],
        secondary: Callable[[], Any] | None = None,
        fallback: Any | None = None,
        stale_cache_key: str | None = None,
        stale_cache_ttl: int | None = None,
    ) -> DegradationResult:
        """Execute the degradation chain.

        Args:
            primary: Primary store query function (sync or async)
            secondary: Secondary/fallback query function
            fallback: Hardcoded fallback value (e.g. empty list, default dict)
            stale_cache_key: If set, try Redis stale cache before giving up
            stale_cache_ttl: TTL override for stale cache lookup

        Returns:
            DegradationResult with data, store_used, and degraded flag.
        """
        start = time.perf_counter()
        stores_tried: list[str] = []
        errors: list[str] = []

        store_name = self._store_name
        timeout = self._store.timeout_seconds if self._store else 3.0

        # ── 1. Try primary store ──────────────────────────────
        if self._registry.should_attempt(store_name):
            try:
                result = await asyncio.wait_for(_to_awaitable(primary), timeout=timeout)
                self._registry.mark_healthy(store_name)
                self._registry.record_success(store_name)
                return DegradationResult(
                    data=result,
                    store_used=store_name,
                    tier_used=DegradationTier.PRIMARY,
                    stores_tried=[store_name],
                    latency_ms=(time.perf_counter() - start) * 1000,
                )
            except TimeoutError:
                err = f"{store_name} timeout ({timeout}s)"
                errors.append(err)
                stores_tried.append(store_name)
                logger.warning(err)
                self._registry.mark_degraded(store_name)
            except Exception as e:
                err = f"{store_name}: {_short_error(e)}"
                errors.append(err)
                stores_tried.append(store_name)
                logger.warning(err)
                self._registry.mark_down(store_name)

        # ── 2. Try secondary store ────────────────────────────
        if secondary:
            sec_name = self._secondary_store_name()
            sec_timeout = self._get_secondary_timeout()
            if self._registry.should_attempt(sec_name):
                try:
                    result = await asyncio.wait_for(_to_awaitable(secondary), timeout=sec_timeout)
                    self._registry.mark_healthy(sec_name)
                    return DegradationResult(
                        data=result,
                        store_used=sec_name,
                        tier_used=DegradationTier.SECONDARY,
                        degraded=True,
                        stores_tried=stores_tried,
                        errors=errors,
                        latency_ms=(time.perf_counter() - start) * 1000,
                    )
                except TimeoutError:
                    err = f"{sec_name} timeout ({sec_timeout}s)"
                    errors.append(err)
                    stores_tried.append(sec_name)
                    logger.warning(err)
                    self._registry.mark_degraded(sec_name)
                except Exception as e:
                    err = f"{sec_name}: {_short_error(e)}"
                    errors.append(err)
                    stores_tried.append(sec_name)
                    logger.warning(err)
                    self._registry.mark_down(sec_name)

        # ── 3. Try stale Redis cache ──────────────────────────
        if stale_cache_key:
            cached = self._registry.lru.get(stale_cache_key)
            if cached is not None:
                logger.info(f"Serving stale cache for {store_name}: {stale_cache_key}")
                return DegradationResult(
                    data=cached,
                    store_used="stale_cache",
                    tier_used=DegradationTier.CACHE,
                    degraded=True,
                    stores_tried=stores_tried,
                    errors=errors,
                    latency_ms=(time.perf_counter() - start) * 1000,
                )

        # ── 4. Return hardcoded fallback ──────────────────────
        logger.error(f"All stores exhausted for {store_name}: {', '.join(stores_tried)}. Errors: {'; '.join(errors)}")
        return DegradationResult(
            data=fallback if fallback is not None else _empty_for_store(store_name),
            store_used="empty",
            tier_used=DegradationTier.EMPTY,
            degraded=True,
            stores_tried=stores_tried,
            errors=errors,
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    async def execute_sync_wrapper(
        self,
        primary: Callable[[], Any],
        secondary: Callable[[], Any] | None = None,
        fallback: Any | None = None,
        stale_cache_key: str | None = None,
    ) -> Any:
        """Execute degradation chain but return just the data (throws on empty)."""
        result = await self.execute(
            primary=primary,
            secondary=secondary,
            fallback=fallback,
            stale_cache_key=stale_cache_key,
        )
        return result.data

    def _secondary_store_name(self) -> str:
        mapping = {
            "clickhouse": "duckdb",
            "neo4j": "postgres",
            "redis": "in_process_lru",
            "minio": "stale_cache",
            "postgres": "duckdb",
        }
        return mapping.get(self._store_name, f"{self._store_name}_secondary")

    def _get_secondary_timeout(self) -> float:
        mapping = {
            "clickhouse": 2.0,
            "neo4j": 2.0,
            "redis": 0.5,
            "minio": 2.0,
            "postgres": 2.0,
        }
        return mapping.get(self._store_name, 2.0)


# ══════════════════════════════════════════════════════════════════════
# PRE-BUILT CHAINS (convenience)
# ══════════════════════════════════════════════════════════════════════


def clickhouse_chain() -> DegradationChain:
    """ClickHouse -> DuckDB -> empty[]"""
    return DegradationChain("clickhouse")


def neo4j_chain() -> DegradationChain:
    """Neo4j -> Postgres -> empty[]"""
    return DegradationChain("neo4j")


def redis_chain() -> DegradationChain:
    """Redis -> InProcessLRU -> degraded"""
    return DegradationChain("redis")


def minio_chain() -> DegradationChain:
    """MinIO -> stale_cache -> file_not_found"""
    return DegradationChain("minio")


def postgres_chain() -> DegradationChain:
    """Postgres -> DuckDB -> empty[]"""
    return DegradationChain("postgres")


# ══════════════════════════════════════════════════════════════════════
# DECORATOR: @degradable
# ══════════════════════════════════════════════════════════════════════


def degradable(
    stores: list[str],
    fallback: Any | None = None,
    stale_cache_key_fn: Callable[..., str] | None = None,
) -> Any:
    """Decorator that wraps an async function with a degradation chain.

    Args:
        stores: Primary store name(s). First is primary, rest are secondary.
                e.g. ["clickhouse", "duckdb"]
        fallback: Hardcoded fallback result if ALL stores fail.
        stale_cache_key_fn: Optional callable(*args, **kwargs) -> str to
                           generate a cache key for stale Redis cache lookup.

    Usage:
        @degradable(stores=["clickhouse"], fallback=[])
        async def get_labels(address: str): ...

        @degradable(stores=["neo4j", "postgres"], fallback={})
        async def resolve_entity(address: str): ...

        @degradable(stores=["redis"], fallback=None,
                     stale_cache_key_fn=lambda addr, **kw: f"auth:{addr}")
        async def check_session(token: str): ...
    """
    primary_store = stores[0]

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            chain = DegradationChain(primary_store)

            # Build the primary call
            async def primary_call() -> Any:
                return await _to_awaitable(func(*args, **kwargs))

            # Generate stale cache key if needed
            cache_key = None
            if stale_cache_key_fn:
                with suppress(ConnectionError, OSError, TimeoutError):
                    cache_key = stale_cache_key_fn(*args, **kwargs)

            result = await chain.execute(
                primary=primary_call,
                fallback=fallback,
                stale_cache_key=cache_key,
            )

            # Always return data — callers CAN check _degradation_result if they want
            # We attach it for middleware inspection
            return result.data

        # Attach metadata for introspection
        wrapper.__degradable__ = True  # type: ignore[attr-defined]
        wrapper.__degradable_store__ = primary_store  # type: ignore[attr-defined]
        return wrapper

    return decorator


# ══════════════════════════════════════════════════════════════════════
# CONTEXT MANAGER
# ══════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def degradation_context(
    store: str,
    primary: Callable[[], Any],
    secondary: Callable[[], Any] | None = None,
    fallback: Any | None = None,
) -> Any:
    """Context manager for degradation chain execution.

    Usage:
        async with degradation_context(
            "clickhouse",
            primary=lambda: query_clickhouse(),
            secondary=lambda: query_duckdb(),
            fallback=[],
        ) as result:
            data = result.data
            if result.degraded:
                logger.warning("Serving degraded data")
    """
    chain = DegradationChain(store)
    result = await chain.execute(
        primary=primary,
        secondary=secondary,
        fallback=fallback,
    )
    yield result


# ══════════════════════════════════════════════════════════════════════
# MIDDLEWARE: X-Degraded-Stores header injection
# ══════════════════════════════════════════════════════════════════════


async def add_degradation_header(response, _request) -> None:
    """FastAPI middleware to inject X-Degraded-Stores header."""
    registry = get_registry()
    header = registry.degraded_header
    if header:
        response.headers["X-Degraded-Stores"] = header


# ══════════════════════════════════════════════════════════════════════
# HEALTH ENDPOINT INTEGRATION
# ══════════════════════════════════════════════════════════════════════


def get_degradation_health() -> dict[str, Any]:
    """Get degradation health status for /health endpoint."""
    registry = get_registry()
    return {
        "degraded_stores": registry.degraded_stores,
        "store_details": registry.health_report(),
        "in_process_lru": registry.lru.stats,
    }


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════


async def _to_awaitable(fn_or_coro: Any) -> Any:
    """Convert a sync function, async function, or coroutine into an awaitable."""
    if asyncio.iscoroutine(fn_or_coro):
        return await fn_or_coro
    if callable(fn_or_coro):
        result = fn_or_coro()
        if asyncio.iscoroutine(result):
            return await result
        return result
    return fn_or_coro


def _short_error(exc: Exception) -> str:
    """Short error description for logging."""
    msg = str(exc)
    return msg[:150] if len(msg) > 150 else msg


def _empty_for_store(store: str) -> Any:
    """Sensible empty default per store type."""
    empties: dict[str, Any] = {
        "clickhouse": [],
        "neo4j": [],
        "redis": None,
        "minio": None,
        "postgres": [],
        "duckdb": [],
    }
    return empties.get(store, [])


def cache_on_success(store: str, key: str, ttl: int = 300) -> None:
    """Cache a value in the in-process LRU on successful primary store result.

    Called manually in route handlers after a successful query.
    """


# ══════════════════════════════════════════════════════════════════════
# CONVENIENCE: Cache successful results for later stale serving
# ══════════════════════════════════════════════════════════════════════


def set_stale_cache(key: str, value: Any, ttl: int | None = None) -> None:
    """Store a value in the in-process LRU for stale cache serving during outages."""
    registry = get_registry()
    registry.lru.set(key, value, ttl)


def get_stale_cache(key: str) -> Any | None:
    """Retrieve a value from the in-process LRU."""
    registry = get_registry()
    return registry.lru.get(key)


logger.info("Degradation system initialized: 5 store types, 3-tier chains, in-process LRU fallback")
