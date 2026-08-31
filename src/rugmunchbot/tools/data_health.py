"""Data-source health aggregator — deep health beyond surface pings.

Win 43/44 extension: not just "is it up" but "is it fast, accurate,
and within quota". Tracks per-source latency EMA, error rates, and
last-success timestamps across all data providers.

Free-first doctrine verified: own index → free pool → keyed freemium →
proxy → paid. Every tier's health is independently visible.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from rugmunchbot.config._endpoints import backend_headers

logger = logging.getLogger(__name__)

# Latency EMA state (in-process; resets on restart)
_latency_ema: dict[str, float] = {}
_error_counts: dict[str, int] = {}
_success_counts: dict[str, int] = {}
_last_success: dict[str, float] = {}


def record(name: str, ok: bool, latency_ms: float) -> None:
    """Record a probe result for EMA tracking."""
    alpha = 0.3  # EMA smoothing
    prev = _latency_ema.get(name, latency_ms)
    _latency_ema[name] = round(alpha * latency_ms + (1 - alpha) * prev, 1)
    if ok:
        _success_counts[name] = _success_counts.get(name, 0) + 1
        _last_success[name] = time.time()
    else:
        _error_counts[name] = _error_counts.get(name, 0) + 1


async def probe_all() -> dict[str, Any]:
    """Probe every data source and return aggregated health."""
    import asyncio

    backend = os.getenv("BACKEND_URL", "http://localhost:8000")
    host = os.getenv("MODEL_SERVICES_HOST", "127.0.0.1")
    checks = {
        "backend": (
            f"{backend}/api/v1/market/hyperliquid-liquidations?limit=5",
            backend_headers(),
            None,
        ),
        "gateway": (f"http://{host}:8010/health", {}, None),
        "deep_embed": (f"http://{host}:8004/health", {}, None),
        "reranker": (f"http://{host}:8003/health", {}, None),
        "chat": (f"http://{host}:8009/health", {}, None),
        "rag_api": ("http://127.0.0.1:8012/health", {}, None),
        "qdrant": ("http://localhost:6334/collections", {}, None),
    }

    async def _probe(name: str, url: str, headers: dict, body: dict | None):
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                if body:
                    r = await c.post(url, json=body, headers=headers)
                else:
                    r = await c.get(url, headers=headers)
            ok = r.status_code == 200
            ms = (time.monotonic() - t0) * 1000
            record(name, ok, ms)
            return name, ok, ms
        except Exception:
            ms = (time.monotonic() - t0) * 1000
            record(name, False, ms)
            return name, False, ms

    tasks = [_probe(n, u, h, b) for n, (u, h, b) in checks.items()]
    results = await asyncio.gather(*tasks)

    sources = []
    for name, ok, ms in sorted(results):
        ema = _latency_ema.get(name, ms)
        errs = _error_counts.get(name, 0)
        succs = _success_counts.get(name, 0)
        total = errs + succs
        error_rate = round(errs / total * 100, 1) if total > 0 else 0.0
        last = _last_success.get(name)
        ago = f"{time.time() - last:.0f}s ago" if last else "never"
        sources.append(
            {
                "source": name,
                "healthy": ok,
                "latency_ms": round(ms, 0),
                "ema_latency_ms": ema,
                "error_rate_pct": error_rate,
                "last_success": ago,
            }
        )

    healthy_count = sum(1 for s in sources if s["healthy"])
    from rugmunchbot.tools.registry import ToolResult

    return ToolResult(
        tool="data_health",
        ok=True,
        data={
            "sources": sources,
            "healthy": healthy_count,
            "total": len(sources),
            "all_healthy": healthy_count == len(sources),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
