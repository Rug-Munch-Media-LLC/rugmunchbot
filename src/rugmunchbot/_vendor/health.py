# VENDORED 2026-08-17 from rmi-backend/app/core/health.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""Health check system for RMI.

Provides a unified health check registry and DomainHealth dataclass
for all domain modules (catalog, rag, token, scanner, etc.).

Architecture:
  - register_health_check(name, func) registers a health check
  - run_health_checks() executes all registered checks
  - DomainHealth captures status for one domain
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.logging_shim import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

log = get_logger(__name__)


@dataclass
class DomainHealth:
    """Health status for one domain (catalog, rag, etc.)."""

    name: str
    healthy: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    latency_ms: int | None = None


# Global health check registry
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
    """Get current health status for all domains."""
    # Sync wrapper for non-async callers
    results = asyncio.run(run_health_checks())
    return {
        "status": (
            "healthy"
            if all(h.healthy for h in results.values())
            else "degraded"
            if any(h.healthy for h in results.values())
            else "unhealthy"
        ),
        "domains": results,
    }
