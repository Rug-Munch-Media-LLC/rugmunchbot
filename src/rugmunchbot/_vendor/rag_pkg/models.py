# VENDORED 2026-08-23 from rmi-backend/app/rag/models.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pydantic v2 models for the RAG domain (thin stub — models kept for backward compat)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EmbeddingProvider(StrEnum):
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    HUGGINGFACE = "huggingface"
    COHERE = "cohere"


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
    query: str = Field(..., min_length=1, max_length=2048)
    collection: str = Field(default="scam_intel")
    top_k: int = Field(default=5, ge=1, le=50)
    min_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    filters: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    content: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection: str = ""
    doc_id: str = ""


class SearchResponse(BaseModel):
    query: str = ""
    hits: list[SearchHit] = Field(default_factory=list)
    took_ms: float = 0.0
    collections_searched: list[str] = Field(default_factory=list)


class IngestRequest(BaseModel):
    collection: str = "scam_intel"
    doc_id: str | None = None
    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    doc_id: str = ""
    collection: str = ""
    status: str = ""
    error: str = ""


class FeedbackRecord(BaseModel):
    token_address: str = ""
    chain: str = ""
    risk_score: float = 0.0
    risk_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
