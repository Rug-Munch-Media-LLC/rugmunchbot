# VENDORED 2026-08-17 from rmi-backend/app/crypto_embeddings/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Crypto Embedder - Multi-head embedding system for crypto scam/rug detection.
============================================================================
Backward-compatible re-exports from focused modules.

Split into: models, features, providers, embedder, seed.
"""

from __future__ import annotations

from rugmunchbot._vendor.crypto_embeddings.embedder import CryptoEmbedder
from rugmunchbot._vendor.crypto_embeddings.features import (
    extract_contract_features,
    extract_transaction_features,
    extract_wallet_features,
)
from rugmunchbot._vendor.crypto_embeddings.models import (
    COLLECTIONS,
    DIMS,
    HEAD_DEFAULTS,
    EmbeddingResult,
)
from rugmunchbot._vendor.crypto_embeddings.providers import (
    ContractCodeEmbedder,
    HuggingFaceEmbedder,
    InfinityBGEEmbedder,
    LocalBGEEmbedder,
    OpenRouterEmbedder,
)
from rugmunchbot._vendor.crypto_embeddings.seed import KNOWN_SCAM_PATTERNS

# ── Singleton ──
_embedder: CryptoEmbedder | None = None


async def get_embedder() -> CryptoEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = CryptoEmbedder()
        await _embedder.initialize()
    return _embedder


__all__ = [
    "COLLECTIONS",
    "DIMS",
    "HEAD_DEFAULTS",
    "KNOWN_SCAM_PATTERNS",
    "ContractCodeEmbedder",
    "CryptoEmbedder",
    "EmbeddingResult",
    "HuggingFaceEmbedder",
    "InfinityBGEEmbedder",
    "LocalBGEEmbedder",
    "OpenRouterEmbedder",
    "extract_contract_features",
    "extract_transaction_features",
    "extract_wallet_features",
    "get_embedder",
]
