# VENDORED 2026-08-17 from rmi-backend/app/rag/config.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RAG pipeline configuration constants.

Centralised configuration for the continuous ingestion pipeline and retrieval.
All tunable knobs live here, not scattered across modules.

Usage:
    from rugmunchbot._vendor.rag_pkg.config import (
        RAG_INGESTION_BATCH_SIZE,
        RAG_INGESTION_MAX_PER_MINUTE,
        RAG_CHUNK_SIZE,
        RAG_CHUNK_OVERLAP,
        RAG_COLLECTIONS,
    )
"""

from __future__ import annotations

# ── Ingestion pipeline ────────────────────────────────────────────────

# Maximum documents per batch before flushing to the embedding store.
RAG_INGESTION_BATCH_SIZE: int = 50

# Hard cap on ingestions per minute (across all collections).
# Prevents the embedding backend (Infinity / OpenRouter) from being
# overwhelmed during bulk backfills or flash crowds.
RAG_INGESTION_MAX_PER_MINUTE: int = 200

# ── Chunking ──────────────────────────────────────────────────────────

# Target chunk size in characters (not tokens — we use char-level for
# simpler deterministic splitting; 512 chars ≈ 128–200 tokens for most
# crypto/English text).
RAG_CHUNK_SIZE: int = 512

# Overlap between consecutive chunks to preserve cross-chunk context.
# 50 chars ≈ 2–3 words of sliding context.
RAG_CHUNK_OVERLAP: int = 50

# Minimum chunk length in characters. Shorter chunks are discarded as
# likely noise (headers, fragments, boilerplate).
RAG_MIN_CHUNK_CHARS: int = 32

# ── Collections ───────────────────────────────────────────────────────

# All active RAG collections. Each is a separate vector index + keyword
# store.  Adding a new data source means adding its collection here.
RAG_COLLECTIONS: list[str] = [
    "scanner",  # Token security scanner results
    "wallet",  # Wallet labels, risk profiles, entity clusters
    "news",  # News articles and market intelligence
    "research",  # Research documents, analysis, bulletin board posts
    "scams",  # Scam pattern documentation, known fraud signatures
]

# ── Quality filter ────────────────────────────────────────────────────

# Minimum content length (characters) for a document to be accepted.
RAG_MIN_DOC_LENGTH: int = 64

# Maximum spam-like repetition ratio (e.g. 0.3 = 30% repeated n-grams).
# Documents above this threshold are rejected as spam.
RAG_SPAM_REPETITION_THRESHOLD: float = 0.3

# ── Query expansion ───────────────────────────────────────────────────

# Number of additional query terms to generate during expansion.
RAG_EXPANSION_TERMS: int = 3

# Weight for vector similarity vs. keyword overlap in hybrid retrieval.
# 1.0 = pure vector; 0.0 = pure keyword.
RAG_HYBRID_ALPHA: float = 0.7

# ── Relevance scoring ─────────────────────────────────────────────────

# RRF constant for reciprocal rank fusion (k=60 is the standard).
RAG_RRF_K: int = 60

# ── Context budget (G2) ──────────────────────────────────────────────
# Hard token budget for the context handed to the LLM. Applied AFTER
# cross-encoder rerank on the primary search facade
# (``app.rag.engine.three_pillar_search``) so the LLM never receives an
# unbounded context. Env override: ``RAG_CONTEXT_MAX_TOKENS``.
RAG_CONTEXT_MAX_TOKENS: int = 4096

# ── RAG regression eval (G3) ─────────────────────────────────────────
# Minimum aggregate metrics for the CI gate (``scripts/rag-eval.sh`` and
# the ``rag_eval`` pytest marker). Env overrides: ``RAG_EVAL_HIT_RATE_MIN``,
# ``RAG_EVAL_FAITHFULNESS_MIN``.
#
# Baselines measured live on the golden set (2026-08-13, threat_intel_v2):
# hit_rate ~0.56-0.73, faithfulness ~0.27-0.34. The floors sit BELOW the
# baseline so the gate fires only on genuine regressions, not noise.
RAG_EVAL_HIT_RATE_MIN: float = 0.5
RAG_EVAL_FAITHFULNESS_MIN: float = 0.25

# ── Ingestion scheduler ───────────────────────────────────────────────

# Default interval (seconds) between scheduled ingestion sweeps.
RAG_SCHEDULER_INTERVAL: int = 300  # 5 minutes
