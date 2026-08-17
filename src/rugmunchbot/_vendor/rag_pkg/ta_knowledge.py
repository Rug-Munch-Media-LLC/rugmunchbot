# VENDORED 2026-08-17 from rmi-backend/app/rag/ta_knowledge.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""TA Methodology Knowledge Base — ingestible patterns for RAG system.

This module contains comprehensive technical analysis methodology that can be
ingested into the RAG vector store via POST /api/v1/rag/v2/bulk-ingest or
POST /api/v1/rag/v2/ingest.

Categories covered:
  1. Classic Chart Patterns (head & shoulders, double top/bottom, cup & handle,
     flags, wedges, triangles)
  2. Indicator Methodology (RSI, MACD, Bollinger Bands, EMA crossovers, volume)
  3. Support/Resistance, Trendlines, Fibonacci Retracements
  4. Volume Analysis & Market Structure (Wyckoff Methodology)
  5. Candlestick Patterns
  6. Risk Management & Position Sizing

Usage:
    from rugmunchbot._vendor.rag_pkg.engine import bulk_ingest
    from rugmunchbot._vendor.rag_pkg.ta_knowledge import TA_KNOWLEDGE_DOCS

    await bulk_ingest(items=TA_KNOWLEDGE_DOCS, collection="ta_methodology")

Re-export shim. During the de-godding pass the module bodies moved to focused
submodules:

  * ``_knowledge`` — the knowledge-base/doc content (``TA_KNOWLEDGE_DOCS``).
  * ``_service`` — query/serving logic: the convenience ingestion helper
    (``ingest_ta_knowledge``) and the availability summary
    (``get_ta_knowledge_summary``).

This module keeps the public API intact: every name is re-exported below so
existing ``app.rag.ta_knowledge`` importers keep working unchanged.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.rag_pkg._knowledge import TA_KNOWLEDGE_DOCS
from rugmunchbot._vendor.rag_pkg._service import get_ta_knowledge_summary, ingest_ta_knowledge

# Explicit re-exports: mypy's ``no_implicit_reexport`` (strict) requires names
# imported from the split submodules and re-exported to external importers to
# be listed here. ``Any`` was module-level in the original ta_knowledge.py and
# is preserved so star-imports and ``from rugmunchbot._vendor.rag_pkg.ta_knowledge import X`` keep
# resolving exactly as before.
__all__ = [
    "TA_KNOWLEDGE_DOCS",
    "Any",
    "get_ta_knowledge_summary",
    "ingest_ta_knowledge",
]
