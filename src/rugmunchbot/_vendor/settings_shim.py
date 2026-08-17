"""Phase 2d settings shim — bot-local replacement for ``app.config.settings``.

The vendored rag/catalog modules read a handful of settings fields. This shim
provides exactly those fields from env vars with the SAME defaults as
rmi-backend's Settings (host/local context — the bot runs on the host, so
``local`` defaults apply). Mutable attributes are supported (rag crawler
assigns ``settings.infinity_url`` for fallback).

Deviations from rmi-backend app.config are limited to these fields; anything
else the vendored code reads must be added here (and backported to the
backend's Settings for consistency).
"""

from __future__ import annotations

import os


def resolve_service_url(name: str, docker: str | None = None, local: str | None = None) -> str:
    """Mirror rmi-backend's service URL resolution (host/local context)."""
    return os.getenv(name, "") or (local or "")


class _Settings:
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    qdrant_url: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    infinity_url: str = os.getenv("INFINITY_URL", "http://localhost:8001")
    bge_m3_url: str = os.getenv("BGE_M3_URL", "")
    embedder_primary: str = os.getenv("EMBEDDER_PRIMARY", "infinity")
    embedder_primary_model: str = os.getenv("EMBEDDER_PRIMARY_MODEL", "BAAI/bge-m3")
    embedder_cache_ttl: int = int(os.getenv("EMBEDDER_CACHE_TTL", "86400"))
    embedder_cache_maxsize: int = int(os.getenv("EMBEDDER_CACHE_MAXSIZE", "10000"))


settings = _Settings()
