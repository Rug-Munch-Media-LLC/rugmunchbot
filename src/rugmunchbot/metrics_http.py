"""Tiny stdlib HTTP server exposing ``/healthz`` and ``/metrics``.

Split out of ``observability`` so that module stays inside the 200-line
budget; the renderers stay there (single source of truth) and are imported
lazily here, which also avoids a circular import.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.metrics")

_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None


class _MetricsHandler(BaseHTTPRequestHandler):
    """Serve /healthz and /metrics. Everything else is 404."""

    def do_GET(self) -> None:
        from rugmunchbot import observability as obs

        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send(
                200, "application/json", json.dumps(obs.health_snapshot(), indent=2).encode()
            )
        elif path == "/metrics":
            body, ctype = obs.render_metrics()
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
