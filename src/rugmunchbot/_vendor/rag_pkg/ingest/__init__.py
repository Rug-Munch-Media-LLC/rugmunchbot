# VENDORED 2026-08-23 from rmi-backend/app/rag/ingest/__init__.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RAG knowledge ingest submodules.

Private (underscore-prefixed) modules split out of the former
``app.rag.ingest_crypto_knowledge`` god file. The public surface is
re-exported unchanged by ``app.rag.ingest_crypto_knowledge`` (thin shim) —
import from there, not from here:

* ``_knowledge_bases.py`` — curated knowledge base data (scam_intel,
  deployer_history, contract_audit, phishing_db, wallet_labels).
* ``_seed_corpus.py``    — curated JSONL seed corpus loader + ingest.
* ``_incidents.py``      — public scam/rug/exploit incident source registry,
  normalization and ingest.
* ``_chunking.py``       — heading-aware doc chunking (markdown/reST/AsciiDoc).
* ``_orchestrator.py``   — shared bulk-ingest client, doc-source ingest,
  knowledge-base ingest, CLI (``main``).

MONKEYPATCH CONTRACT (same as the chat_ai/adminbot splits): the pipeline
tests patch ``app.rag.ingest_crypto_knowledge.<dedup-name>`` at runtime
(``is_duplicate_url``, ``mark_url_ingested``, ...), so the submodules resolve
those names through the shim module object (``_ingest.X``) at call time —
never via ``from rugmunchbot._vendor.dedup import X``. Do not change that wiring.
"""
