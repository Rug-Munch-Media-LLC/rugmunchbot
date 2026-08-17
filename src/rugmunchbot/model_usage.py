#!/usr/bin/env python3
"""RugMunchBot -- lightweight in-memory model-usage counter.

Records every successful LLM chat call (tier / model / latency / tokens) so
the owner admin commands (``/admin``, ``/credits``) can report total chat
calls, calls per model (snappy / long / fallback / backend), tokens used and
latency without a schema change or a Redis dependency.

The bot runs as a single long-lived process (systemd, ``concurrent_updates=4``),
so a thread-safe in-process dict is the right scale: zero failure modes, zero
setup, and the counters survive as long as the process (``/admin`` reads the
same snapshot the pipeline writes). The trace logs (``chat_answer_trace``)
remain the durable audit trail; this module is the fast aggregated view.

Tiers recorded by the chat pipeline:
  * ``backend``  — tier-1 backend RAG chat (POST /api/v1/chat)
  * ``snappy``   — free OpenRouter fast tier (poolside/laguna-s-2.1:free)
  * ``long``     — free OpenRouter deep tier (nvidia/nemotron-3-ultra-550b:free)
  * ``fallback`` — the cheap PAID ``:lowest`` model after a free-tier failure

Thread safety: ``threading.Lock`` guards every mutation; ``snapshot()``
returns a JSON-safe copy under the same lock so ``/admin`` never sees a torn
counter. Never raises — recording is best-effort (fail-open).
"""

from __future__ import annotations

import contextlib
import threading
import time
from datetime import UTC, datetime
from typing import Any

_lock = threading.Lock()

# tier -> {"calls": int, "tokens": int, "latency_ms": float}
_tiers: dict[str, dict[str, Any]] = {}
# model -> {"calls": int, "tokens": int}
_models: dict[str, dict[str, int]] = {}
# YYYY-MM-DD -> {"calls": int, "tokens": int} (UTC)
_days: dict[str, dict[str, int]] = {}

_total_calls = 0
_total_tokens = 0
_total_latency_ms = 0.0
_started_at = time.monotonic()


def record(tier: str, model: str = "", latency_ms: float = 0.0, tokens: int = 0) -> None:
    """Record one LLM chat call. Never raises; best-effort counters."""
    global _total_calls, _total_tokens, _total_latency_ms
    tier = (tier or "unknown").strip() or "unknown"
    model = (model or "").strip()
    latency_ms = max(0.0, float(latency_ms or 0.0))
    tokens = max(0, int(tokens or 0))
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    with _lock:
        _total_calls += 1
        _total_tokens += tokens
        _total_latency_ms += latency_ms

        bucket = _tiers.setdefault(tier, {"calls": 0, "tokens": 0, "latency_ms": 0.0})
        bucket["calls"] += 1
        bucket["tokens"] += tokens
        bucket["latency_ms"] += latency_ms

        if model:
            m = _models.setdefault(model, {"calls": 0, "tokens": 0})
            m["calls"] += 1
            m["tokens"] += tokens

        d = _days.setdefault(day, {"calls": 0, "tokens": 0})
        d["calls"] += 1
        d["tokens"] += tokens

    # Mirror into Prometheus/observability counters (best-effort, never raises).
    with contextlib.suppress(Exception):
        from app.domains.telegram.rugmunchbot.observability import record_chat

        record_chat(tier, model, latency_ms)


def snapshot() -> dict[str, Any]:
    """Return a JSON-safe snapshot of every counter (never raises)."""
    with _lock:
        tiers = {
            tier: {"calls": b["calls"], "tokens": b["tokens"], "latency_ms": round(b["latency_ms"], 1)}
            for tier, b in sorted(_tiers.items())
        }
        models = {
            model: {"calls": m["calls"], "tokens": m["tokens"]}
            for model, m in sorted(_models.items(), key=lambda kv: kv[1]["calls"], reverse=True)
        }
        days = {d: dict(v) for d, v in sorted(_days.items())}
        return {
            "total_calls": _total_calls,
            "total_tokens": _total_tokens,
            "total_latency_ms": round(_total_latency_ms, 1),
            "uptime_secs": round(time.monotonic() - _started_at, 1),
            "tiers": tiers,
            "models": models,
            "days": days,
        }
