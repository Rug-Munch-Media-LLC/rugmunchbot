# VENDORED 2026-08-17 from rmi-backend/app/rag/models.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pydantic v2 models for the RAG domain."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EmbeddingProvider(StrEnum):
    """Embedding provider options."""

    OPENAI = "openai"
    OPENROUTER = "openrouter"
    HUGGINGFACE = "huggingface"
    COHERE = "cohere"


# Default RAG collections (matches legacy app.rag_engine.COLLECTIONS)
# ``scanned_tokens`` is a Qdrant-only collection written by the bot
# scan-to-RAG memory bridge (app.rag.scan_memory) — included so generic
# multi-collection RAG searches (and the engine's Qdrant fallback) can
# recall past token scan context.
# ``stablecoin_market`` is a Qdrant-only collection written by
# scripts/ingest_stablecoin_market.py (DeFiLlama stablecoin market caps)
# — included so generic market-context queries can recall stablecoin
# liquidity signals.
# ``scanned_wallets`` is a Qdrant-only collection written by the wallet
# trust feed (app.rag.scan_memory.index_wallet_memory) — the labeling
# system accumulates wallet trust/verdicts over time.
COLLECTIONS: list[str] = [
    "scam_intel",
    "deployer_history",
    "wallet_labels",
    "contract_audit",
    "phishing_db",
    "scanned_tokens",
    "scanned_wallets",
    "stablecoin_market",
]


class SearchRequest(BaseModel):
    """RAG search request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., min_length=1, max_length=2048)
    collection: str = Field(default="scam_intel")
    top_k: int = Field(default=5, ge=1, le=50)
    min_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    filters: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    """A single search result."""

    content: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection: str = ""
    doc_id: str = ""


class SearchResponse(BaseModel):
    """RAG search response."""

    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    total: int = 0
    took_ms: int = 0
    collection: str = ""


class IngestRequest(BaseModel):
    """RAG document ingestion request."""

    model_config = ConfigDict(str_strip_whitespace=True)

    collection: str = Field(default="scam_intel")
    content: str = Field(..., min_length=1)
    doc_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    """RAG document ingestion result."""

    doc_id: str
    collection: str
    status: str = "ok"  # ok | failed
    chunks: int = 0
    error: str | None = None
    ingested_at: datetime = Field(default_factory=datetime.utcnow)


class FeedbackRecord(BaseModel):
    """Scanner → RAG feedback record."""

    model_config = ConfigDict(str_strip_whitespace=True)

    token_address: str
    chain: str = "solana"
    safety_score: float
    risk_flags: list[str] = Field(default_factory=list)
    action: str = "ingest"  # ingest | remove | update
    source: str = "scanner"
