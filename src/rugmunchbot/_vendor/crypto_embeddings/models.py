# VENDORED 2026-08-17 from rmi-backend/app/crypto_embeddings/models.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Embedding configuration and data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Embedding dimensions by model
DIMS = {
    "local/bge-small-en-v1.5": 384,
    "openai/text-embedding-3-large": 3072,
    "openai/text-embedding-3-small": 1536,
    "BAAI/bge-m3": 1024,
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-small-en-v1.5": 384,
    "nvidia/llama-nemotron-embed-vl-1b-v2:free": 2048,
    "rmi/qwen3-embedding:4b": 2048,
    "crypto_behavioral": 64,
    "crypto_code_hash": 128,
}

HEAD_DEFAULTS = {
    "semantic": "rmi/bge-m3",
    "code": "rmi/bge-m3",
    "behavioral": "crypto_behavioral",
    "entity": "crypto_behavioral",
}

OR_PRIMARY_EMBED = "rmi/bge-m3"
OR_PAID_FALLBACK = "nvidia/llama-nemotron-embed-vl-1b-v2:free"
OR_FALLBACK_EMBED = "local/bge-small-en-v1.5"
NVIDIA_EMBED_URL = "https://integrate.api.nvidia.com/v1/embeddings"

COLLECTIONS = [
    "wallet_profiles",
    "token_analysis",
    "scam_patterns",
    "forensic_reports",
    "market_intel",
    "contract_audits",
    "known_scams",
    "news_articles",
    "transaction_patterns",
    "defi_hacks",
    "rug_timeline",
    "vuln_patterns",
    "crime_reports",
]


@dataclass
class EmbeddingResult:
    vector: list[float]
    dims: int
    model: str
    head: str
    metadata: dict[str, Any] = field(default_factory=dict)
    cached: bool = False
