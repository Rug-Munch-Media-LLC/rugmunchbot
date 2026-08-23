"""Phase 2d settings shim — bot-local replacement for ``app.config.settings``.

llama.cpp qwen3/qwen3-embedding-4b fully replaced by Qwen3-Embedding-4B via llama.cpp
(local host, port 8004, 2560d). Updated 2026-08-20.

The vendored rag/catalog modules read a handful of settings fields. This shim
provides exactly those fields from env vars with the SAME defaults as
rmi-backend's Settings (host/local context — the bot runs on the host, so
``local`` defaults apply). Mutable attributes are supported (rag crawler
assigns ``settings.qwen_embed_url`` for fallback).

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
    # Qwen3-Embedding-4B via llama.cpp (replaces old llama.cpp qwen3 qwen3-embedding-4b, 2560d)
    qwen_embed_url: str = os.getenv("QWEN_EMBED_URL", "http://172.22.0.1:8004/v1")
    # Qwen3-Reranker-4B via llama.cpp (separate process, port 8003, docker bridge)
    reranker_url: str = os.getenv("RERANKER_URL", "http://172.22.0.1:8003")
    bge_m3_url: str = os.getenv("BGE_M3_URL", "")
    searxng_base_url: str = os.getenv("SEARXNG_BASE_URL", "http://localhost:8889")
    embedder_primary: str = os.getenv("EMBEDDER_PRIMARY", "llama.cpp qwen3")
    embedder_primary_model: str = os.getenv("EMBEDDER_PRIMARY_MODEL", "qwen3-embedding-4b")
    embedder_cache_ttl: int = int(os.getenv("EMBEDDER_CACHE_TTL", "86400"))
    embedder_cache_maxsize: int = int(os.getenv("EMBEDDER_CACHE_MAXSIZE", "10000"))


settings = _Settings()


class _CompatAlias:
    pass
