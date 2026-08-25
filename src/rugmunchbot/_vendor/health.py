# VENDORED 2026-08-24 from rmi-backend/app/core/health.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Central health/readiness probes for the RMI backend.

``/healthz`` (liveness) — process is up; no dependency checks.
``/readyz``  (readiness) — probes Postgres, Redis, Qdrant with short
timeouts; degrades gracefully (a dependency being down never raises).

Each probe returns ``{"ok": bool, "latency_ms": int, "error": str | None}``.
Aggregated status is ``"healthy"`` only when every enabled dependency answers.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

import httpx
from fastapi.responses import JSONResponse

from rugmunchbot._vendor.settings_shim import settings

PROBE_TIMEOUT_SECONDS = 2.0

log = logging.getLogger(__name__)


@dataclass
class DomainHealth:
    """Health status for one domain (catalog, rag, alerts, etc.).

    Restored 2026-08-21: the /healthz centralization commit removed this
    dataclass but seven domain modules still construct it in their
    ``register_health_checks`` callbacks.
    """

    name: str
    healthy: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_ms: int | None = None


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _probe_result(ok: bool, start: float, error: str | None = None) -> dict[str, Any]:
    return {"ok": ok, "latency_ms": _elapsed_ms(start), "error": error}


async def check_database() -> dict[str, Any]:
    """Probe Postgres with a trivial SELECT. Fail-safe."""
    start = time.perf_counter()
    try:
        from sqlalchemy import text

        database_url = str(settings.database_url)
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(database_url, pool_pre_ping=False)
        try:
            async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()
        return _probe_result(True, start)
    except Exception as exc:
        return _probe_result(False, start, f"{type(exc).__name__}: {exc}")


async def check_redis() -> dict[str, Any]:
    """Probe Redis with a PING. Fail-safe."""
    start = time.perf_counter()
    try:
        import redis as sync_redis

        client = sync_redis.Redis.from_url(
            str(settings.redis_url),
            decode_responses=True,
            socket_timeout=PROBE_TIMEOUT_SECONDS,
            socket_connect_timeout=PROBE_TIMEOUT_SECONDS,
        )
        try:
            pong = client.ping()
        finally:
            client.close()
        return _probe_result(bool(pong), start)
    except Exception as exc:
        return _probe_result(False, start, f"{type(exc).__name__}: {exc}")


async def check_qdrant() -> dict[str, Any]:
    """Probe Qdrant's HTTP API. Fail-safe; skipped when unconfigured."""
    start = time.perf_counter()
    try:
        qdrant_url = str(getattr(settings, "qdrant_url", "") or "")
        if not qdrant_url:
            return {"ok": True, "latency_ms": 0, "error": None, "skipped": "not configured"}
        # 2026-08-25: no port rewriting — host-published v2 answers HTTP on
        # :6334 (:6333 here is the OLD qdrant container).
        base = qdrant_url
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            r = await client.get(f"{base}/collections")
            r.raise_for_status()
        return _probe_result(r.status_code == 200, start)
    except Exception as exc:
        return _probe_result(False, start, f"{type(exc).__name__}: {exc}")


async def get_readiness() -> dict[str, Any]:
    """Aggregate readiness across all dependencies (concurrent probes)."""
    results = await asyncio.gather(check_database(), check_redis(), check_qdrant(), return_exceptions=True)
    deps: dict[str, Any] = {}
    for name, res in zip(("database", "redis", "qdrant"), results, strict=True):
        if isinstance(res, Exception):  # pragma: no cover — probes never raise
            deps[name] = {"ok": False, "latency_ms": -1, "error": str(res)}
        else:
            deps[name] = res
    all_ok = all(d.get("ok") for d in deps.values())
    return {"status": "healthy" if all_ok else "degraded", "dependencies": deps}


async def healthz() -> dict[str, str]:
    """Liveness: process is up. Never probes dependencies — a down dependency
    must not cause the orchestrator to restart a healthy process."""
    return {"status": "alive"}


async def readyz() -> JSONResponse:
    """Readiness: aggregate dependency state; 503 when degraded."""
    readiness = await get_readiness()
    code = 200 if readiness["status"] == "healthy" else 503
    return JSONResponse(status_code=code, content=readiness)


def register_health_routes(app: Any) -> None:
    """Mount ``/healthz`` (liveness) + ``/readyz`` (readiness) on the app."""
    app.add_api_route("/healthz", healthz, methods=["GET"], include_in_schema=False)
    app.add_api_route("/readyz", readyz, methods=["GET"], include_in_schema=False)


# ── Domain health-check registry (restored 2026-08-21) ──────────────
# The /healthz centralization removed this registry, but seven domains
# register callbacks into it via register_health_check().
_health_checks: dict[str, Callable[[], Any]] = {}


def register_health_check(name: str, func: Callable[[], Any]) -> None:
    """Register a health check function for a domain."""
    if name in _health_checks:
        log.warning(f"health_check_already_registered name={name}")
    _health_checks[name] = func


async def run_health_checks() -> dict[str, DomainHealth]:
    """Run all registered health checks and return results."""
    results: dict[str, DomainHealth] = {}
    for name, func in _health_checks.items():
        try:
            start = asyncio.get_event_loop().time()
            result = func()
            if asyncio.iscoroutine(result):
                result = await result
            took_ms = int((asyncio.get_event_loop().time() - start) * 1000)
            results[name] = DomainHealth(
                name=name,
                healthy=result.healthy if hasattr(result, "healthy") else bool(result),
                details=getattr(result, "details", {}),
                error=getattr(result, "error", None),
                latency_ms=took_ms,
            )
        except Exception as e:
            log.exception(f"health_check_failed name={name}")
            results[name] = DomainHealth(name=name, healthy=False, error=str(e))
    return results


def get_health_status() -> dict[str, Any]:
    """Sync wrapper: run all registered domain checks (non-async callers)."""
    import asyncio as _aio

    results = _aio.run(run_health_checks())
    return {
        "status": (
            "healthy"
            if all(d.healthy for d in results.values())
            else "degraded"
            if any(d.healthy for d in results.values())
            else "unhealthy"
        ),
        "domains": {
            n: {"healthy": d.healthy, "error": d.error, "latency_ms": d.latency_ms} for n, d in results.items()
        },
    }
