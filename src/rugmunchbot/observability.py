#!/usr/bin/env python3
"""RugMunchBot -- lightweight enterprise observability (health + metrics).

Serves two endpoints from the bot process (which otherwise exposes no HTTP):

  * ``GET /healthz`` — JSON health/liveness for the systemd unit / load
    balancer: ``status``, ``uptime_seconds``, ``scheduler_running``,
    ``last_error`` and ``version``. Always 200 while the process is up.
  * ``GET /metrics``  — Prometheus text exposition format (``prometheus-client``
    is an existing project dependency). Falls back to a plain JSON payload when
    ``prometheus_client`` is not importable, so the bot never fails to start
    because of a missing optional lib.

Metrics exposed (``rmi_bot_*``):

  * ``rmi_bot_chat_calls_total{tier,model}``      — LLM chat calls, mirrors
    ``model_usage`` (backend / snappy / long / fallback).
  * ``rmi_bot_chat_latency_seconds{tier}``        — chat latency histogram.
  * ``rmi_bot_scans_total{kind}``                 — /scan, /deepscan, sentinel.
  * ``rmi_bot_errors_total{kind}``                — exceptions + failed jobs.
  * ``rmi_bot_webhook_events_total{event_type}``  — intelligent webhook events
    delivered to users (alert worker).
  * ``rmi_bot_jobs_total{name}`` / ``rmi_bot_jobs_failed_total{name}`` — scheduled
    job runs/failures.
  * ``rmi_bot_uptime_seconds``, ``rmi_bot_scheduler_running``,
    ``rmi_bot_last_error_timestamp_seconds`` — gauges.

Binding: ``RUGMUNCHBOT_METRICS_HOST`` (default ``127.0.0.1``) and
``RUGMUNCHBOT_METRICS_PORT`` (default ``9191``). The server runs in a daemon
thread so it never blocks the PTB event loop and dies with the process.

Every recording helper is best-effort and never raises — observability must
not take the bot down.
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.observability")

BOT_VERSION = "6"

_START = time.monotonic()
_SCHEDULER_RUNNING = False
_LAST_ERROR: str | None = None
_LAST_ERROR_AT = 0.0

_lock = threading.Lock()

try:  # prometheus-client is an existing project dependency (requirements.txt)
    from prometheus_client import REGISTRY, Counter, Gauge, Histogram, generate_latest

    _HAS_PROMETHEUS = True

    CHAT_CALLS = Counter("rmi_bot_chat_calls_total", "LLM chat calls by tier/model", ["tier", "model"])
    CHAT_LATENCY = Histogram("rmi_bot_chat_latency_seconds", "LLM chat latency by tier", ["tier"])
    SCANS = Counter("rmi_bot_scans_total", "Token scan commands by kind", ["kind"])
    ERRORS = Counter("rmi_bot_errors_total", "Bot errors by kind", ["kind"])
    WEBHOOK_EVENTS = Counter("rmi_bot_webhook_events_total", "Webhook events delivered by type", ["event_type"])
    JOBS_RUN = Counter("rmi_bot_jobs_total", "Scheduled job runs by name", ["name"])
    JOBS_FAILED = Counter("rmi_bot_jobs_failed_total", "Scheduled job failures by name", ["name"])
    UPTIME = Gauge("rmi_bot_uptime_seconds", "Bot process uptime")
    SCHEDULER_GAUGE = Gauge("rmi_bot_scheduler_running", "Scheduler running (1/0)")
    LAST_ERROR_GAUGE = Gauge("rmi_bot_last_error_timestamp_seconds", "Unix timestamp of last error")
except ImportError:  # pragma: no cover - fallback path only when prometheus-client is absent
    _HAS_PROMETHEUS = False
    REGISTRY = None  # type: ignore[assignment]
    generate_latest = None  # type: ignore[assignment]
    CHAT_CALLS = SCANS = ERRORS = WEBHOOK_EVENTS = JOBS_RUN = JOBS_FAILED = None  # type: ignore[assignment]
    CHAT_LATENCY = UPTIME = SCHEDULER_GAUGE = LAST_ERROR_GAUGE = None  # type: ignore[assignment]
    # Plain fallback registry: key -> count (keys embed labels as JSON)
    _plain: dict[str, int] = {}
    # tier -> [latency_sum_ms, count]
    _plain_latency: dict[str, list[float]] = {}


# ── Record helpers (never raise) ────────────────────────────────────────


def _inc(counter: Any, plain_key: str, labels: dict[str, str] | None = None) -> None:
    """Increment a Prometheus counter, or the plain fallback registry."""
    if _HAS_PROMETHEUS and counter is not None:
        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
        return
    key = plain_key + (json.dumps(labels, sort_keys=True) if labels else "")
    with _lock:
        _plain[key] = _plain.get(key, 0) + 1


def record_chat(tier: str, model: str = "", latency_ms: float = 0.0) -> None:
    """Record one LLM chat call (mirrors ``model_usage.record``)."""
    tier = (tier or "unknown").strip() or "unknown"
    model = (model or "unknown").strip() or "unknown"
    _inc(CHAT_CALLS, "chat_calls", {"tier": tier, "model": model})
    latency_ms = max(0.0, float(latency_ms or 0.0))
    if _HAS_PROMETHEUS:
        CHAT_LATENCY.labels(tier=tier).observe(latency_ms / 1000.0)
    else:
        with _lock:
            bucket = _plain_latency.setdefault(tier, [0.0, 0.0])
            bucket[0] += latency_ms
            bucket[1] += 1.0


def record_scan(kind: str = "scan") -> None:
    """Record one token scan command (/scan, /deepscan, sentinel)."""
    _inc(SCANS, "scans", {"kind": (kind or "scan").strip() or "scan"})


def record_webhook_event(event_type: str = "unknown") -> None:
    """Record one intelligent webhook event delivered to a user."""
    _inc(WEBHOOK_EVENTS, "webhook_events", {"event_type": (event_type or "unknown").strip() or "unknown"})


def record_job(name: str = "unknown") -> None:
    """Record one scheduled job run."""
    _inc(JOBS_RUN, "jobs", {"name": (name or "unknown").strip() or "unknown"})


def record_error(kind: str = "unknown", message: str = "") -> None:
    """Record one error (exception / failed job) and remember the last one."""
    global _LAST_ERROR, _LAST_ERROR_AT
    with _lock:
        _LAST_ERROR = str(message or kind)[:500]
        _LAST_ERROR_AT = time.time()
    _inc(ERRORS, "errors", {"kind": (kind or "unknown").strip() or "unknown"})
    if _HAS_PROMETHEUS:
        LAST_ERROR_GAUGE.set(_LAST_ERROR_AT)


def set_scheduler_running(running: bool) -> None:
    """Flip the scheduler-running gauge (job_queue present / stopped)."""
    global _SCHEDULER_RUNNING
    _SCHEDULER_RUNNING = bool(running)
    if _HAS_PROMETHEUS:
        SCHEDULER_GAUGE.set(1 if _SCHEDULER_RUNNING else 0)


# ── Rendering ───────────────────────────────────────────────────────────


def _render_metrics() -> tuple[bytes, str]:
    """Return (body, content_type) for /metrics."""
    if _HAS_PROMETHEUS and REGISTRY is not None and generate_latest is not None:
        UPTIME.set(time.monotonic() - _START)
        SCHEDULER_GAUGE.set(1 if _SCHEDULER_RUNNING else 0)
        return generate_latest(REGISTRY), "text/plain; version=0.0.4; charset=utf-8"

    # Plain JSON fallback (no prometheus-client available).
    payload: dict[str, Any] = {
        "uptime_seconds": round(time.monotonic() - _START, 1),
        "scheduler_running": _SCHEDULER_RUNNING,
        "counters": {},
        "chat_latency_ms_mean": {},
    }
    with _lock:
        for key, count in sorted(_plain.items()):
            payload["counters"][key] = count
        for tier, (lat_total, lat_count) in sorted(_plain_latency.items()):
            payload["chat_latency_ms_mean"][tier] = round(lat_total / lat_count, 1) if lat_count else 0.0
        payload["last_error"] = _LAST_ERROR
    return json.dumps(payload, indent=2).encode(), "application/json"


def _render_health() -> dict[str, Any]:
    """Snapshot for /healthz — plain JSON, always 200 while the process is up."""
    with _lock:
        last_error = _LAST_ERROR
        last_error_at = _LAST_ERROR_AT
    return {
        "status": "ok",
        "version": BOT_VERSION,
        "uptime_seconds": round(time.monotonic() - _START, 1),
        "scheduler_running": _SCHEDULER_RUNNING,
        "last_error": last_error,
        "last_error_at": last_error_at,
    }


# ── Tiny HTTP server (daemon thread, stdlib only) ───────────────────────


class _MetricsHandler(BaseHTTPRequestHandler):
    """Serve /healthz and /metrics. Everything else is 404."""

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/healthz":
            body = json.dumps(_render_health(), indent=2).encode()
            self._send(200, "application/json", body)
        elif self.path.split("?", 1)[0] == "/metrics":
            body, ctype = _render_metrics()
            self._send(200, ctype, body)
        else:
            self._send(404, "text/plain", b"not found\n")

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("metrics_http " + format, *args)


_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None


def start_server() -> None:
    """Start the /healthz + /metrics server in a daemon thread (idempotent)."""
    global _server, _server_thread
    if _server is not None:
        return
    host = os.getenv("RUGMUNCHBOT_METRICS_HOST", "127.0.0.1")
    try:
        port = int(os.getenv("RUGMUNCHBOT_METRICS_PORT", "9191"))
    except ValueError:
        port = 9191
    try:
        _server = ThreadingHTTPServer((host, port), _MetricsHandler)
        _server.daemon_threads = True
    except OSError as e:
        logger.warning("metrics_server_start_failed host=%s port=%s err=%s", host, port, e)
        _server = None
        return
    _server_thread = threading.Thread(target=_server.serve_forever, name="bot-metrics", daemon=True)
    _server_thread.start()
    logger.info("metrics_server_started host=%s port=%s", host, port)


def stop_server() -> None:
    """Stop the metrics HTTP server (idempotent, best-effort)."""
    global _server, _server_thread
    srv, _server = _server, None
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            logger.debug("metrics_server_shutdown_error", exc_info=True)
        try:
            srv.server_close()
        except Exception:
            logger.debug("metrics_server_close_error", exc_info=True)
    _server_thread = None
