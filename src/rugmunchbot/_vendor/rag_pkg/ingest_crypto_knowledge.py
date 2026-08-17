# VENDORED 2026-08-17 from rmi-backend/app/rag/ingest_crypto_knowledge.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
#!/usr/bin/env python3
"""
Crypto Knowledge Ingestion Script — re-export shim
==================================================
Populates RAG vector collections with curated crypto security knowledge.

Usage:
    python app/rag/ingest_crypto_knowledge.py [--base-url http://localhost:8000]
                                              [--collection scam_intel]
                                              [--dry-run]
                                              [--incidents]
                                              [--docs]
                                              [--seed [SEED_FILE]]
                                              [--force]

Idempotent + resumable: every path (knowledge bases, incidents, docs, seed
corpus) records per-item dedup marks (URL + content hash) via
``app.rag.dedup`` before/after ingestion. Re-running the script skips items
already ingested within the dedup TTL window, so an interrupted run resumes
from where it stopped. Pass ``--force`` to bypass the marks and re-ingest
everything. All dedup checks fail open (backend down → proceed), so the
script never deadlocks on Redis/Qdrant outages.

Collections populated:
    scam_intel       — Common scam patterns, honeypots, rug pulls, flash loans
    deployer_history — Historical incidents (LUNA, FTX, Squid Game, etc.)
    contract_audit   — Token analysis methodology, audit patterns
    phishing_db      — DeFi concepts, terminology, security concepts
    wallet_labels    — Technical analysis patterns (support/resistance, indicators)

``--incidents`` additionally fetches public scam/rug/exploit incident feeds
(crawl_sources.json → incident_sources: DefiLlama hacks, REKT leaderboard,
SlowMist hacked, CertiK alerts), normalizes each record into a consistent
shape and ingests it into the matching RAG collection(s) (known_scams,
scam_patterns, defi_exploits, threat_intel). Best-effort: network failures
log and return empty — never raise.

``--docs`` fetches the raw markdown / reST / AsciiDoc knowledge sources in the
``education`` category (crawl_sources.json → incident_sources: ConsenSys
smart-contract-best-practices, crytic/not-so-smart-contracts, kadenzipfel/
smart-contract-vulnerabilities, Solidity security-considerations, OpenZeppelin
upgradeable docs), splits each doc into heading-aware chunks, dedupes by URL +
content hash and ingests into the target collection(s) (coding_knowledge,
threat_intel). Fail-open: a source that 404s / times out / yields no usable
chunks is skipped and recorded — never raise.

``--seed [SEED_FILE]`` ingests the curated seed corpus (default
``app/rag/seed_corpus/crypto_knowledge.jsonl``) — 50-100 hand-written entries
covering definitions, scam patterns, TA terms, market concepts and security
best practices. Entries carry ``source="seed-corpus"`` attribution and are
routed to the matching legacy collection by category. Each entry is deduped by
its stable synthetic URL (``https://seed-corpus.invalid/<id>``) plus its
content hash per target collection — fail-open like the other paths.

Since the de-godding split (2026-08-14) this module is a THIN RE-EXPORT SHIM —
all logic lives in ``app.rag.ingest.*`` submodules:

  * ``ingest/_knowledge_bases.py`` — curated knowledge base data
  * ``ingest/_seed_corpus.py``     — curated JSONL seed corpus loader + ingest
  * ``ingest/_incidents.py``       — public incident sources, normalization + ingest
  * ``ingest/_chunking.py``        — heading-aware doc chunking
  * ``ingest/_orchestrator.py``    — bulk-ingest client, doc sources, CLI (``main``)

Every public name and signature is unchanged — ``from
app.rag.ingest_crypto_knowledge import X`` (cron/scripts, other rag modules,
tests) resolves exactly as before. The dedup names (``content_hash``,
``is_duplicate_url``, ``is_duplicate_content``, ``mark_url_ingested``,
``mark_content_ingested``) are re-exported from ``app.rag.dedup`` and the
submodules resolve them through this module at call time, so runtime patches
on this module (see tests) keep working.
"""

from __future__ import annotations

import sys

if __name__ == "__main__":
    # Script execution (``python app/rag/ingest_crypto_knowledge.py``): this
    # file lives under ``__main__``, so the submodules' ``import
    # app.rag.ingest_crypto_knowledge as _ingest`` would re-import it fresh and
    # deadlock on a circular import. Alias the canonical module name to this
    # object before importing the submodules so ``_ingest.X`` resolves here.
    sys.modules["app.rag.ingest_crypto_knowledge"] = sys.modules[__name__]

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg._crawler_constants import (
    INCIDENT_CATEGORIES,
    INCIDENT_CATEGORY_BY_SOURCE,
    INCIDENT_COLLECTION_BY_CATEGORY,
    INCIDENT_SOURCES_FILE,
)
from rugmunchbot._vendor.rag_pkg.config import RAG_CHUNK_OVERLAP, RAG_CHUNK_SIZE, RAG_MIN_CHUNK_CHARS
from rugmunchbot._vendor.dedup import (
    content_hash,
    is_duplicate_content,
    is_duplicate_url,
    mark_content_ingested,
    mark_url_ingested,
)
from rugmunchbot._vendor.rag_pkg.ingest._chunking import (
    _ADOC_HEADING_RE,
    _DOC_HEADING_RE,
    _RST_UNDERLINE_RE,
    _doc_payload,
    _normalize_doc_headings,
    _recursive_char_split,
    _split_at_headings,
    split_doc_into_chunks,
)
from rugmunchbot._vendor.rag_pkg.ingest._incidents import (
    _REQUIRED_SOURCE_FIELDS,
    _VALID_SEVERITIES,
    _extract_records,
    _incident_doc_id,
    _incident_payload,
    _incident_url,
    _normalize_amount,
    _normalize_chain,
    _normalize_date,
    _normalize_severity,
    _synthetic_incident_url,
    collection_names_for_category,
    dedupe_incidents,
    fetch_incident_source,
    ingest_incidents_from_sources,
    load_incident_sources,
    normalize_incident,
)
from rugmunchbot._vendor.rag_pkg.ingest._knowledge_bases import (
    CONTRACT_AUDIT,
    DEPLOYER_HISTORY,
    KNOWLEDGE_BASES,
    PHISHING_DB,
    SCAM_INTEL,
    WALLET_LABELS,
)
from rugmunchbot._vendor.rag_pkg.ingest._orchestrator import (
    _BULK_CHUNK_SIZE,
    _write_headers,
    fetch_doc_source,
    ingest_all,
    ingest_collection,
    ingest_doc_sources,
    load_doc_sources,
    main,
    verify_ingestion,
)
from rugmunchbot._vendor.rag_pkg.ingest._seed_corpus import (
    _SEED_REQUIRED_FIELDS,
    SEED_CATEGORY_COLLECTIONS,
    SEED_CORPUS_DEFAULT_PATH,
    _seed_payload,
    _seed_synthetic_url,
    ingest_seed_corpus,
    load_seed_corpus,
)

log = get_logger(__name__)

__all__ = [
    # Full original module surface — public functions, data + constants and
    # the underscore-prefixed helpers imported by tests/scripts. Sorted by
    # ruff RUF022 (case-sensitive: constants → _-prefixed → lower-case).
    "CONTRACT_AUDIT",
    "DEPLOYER_HISTORY",
    "INCIDENT_CATEGORIES",
    "INCIDENT_CATEGORY_BY_SOURCE",
    "INCIDENT_COLLECTION_BY_CATEGORY",
    "INCIDENT_SOURCES_FILE",
    "KNOWLEDGE_BASES",
    "PHISHING_DB",
    "RAG_CHUNK_OVERLAP",
    "RAG_CHUNK_SIZE",
    "RAG_MIN_CHUNK_CHARS",
    "SCAM_INTEL",
    "SEED_CATEGORY_COLLECTIONS",
    "SEED_CORPUS_DEFAULT_PATH",
    "WALLET_LABELS",
    "_ADOC_HEADING_RE",
    "_BULK_CHUNK_SIZE",
    "_DOC_HEADING_RE",
    "_REQUIRED_SOURCE_FIELDS",
    "_RST_UNDERLINE_RE",
    "_SEED_REQUIRED_FIELDS",
    "_VALID_SEVERITIES",
    "_doc_payload",
    "_extract_records",
    "_incident_doc_id",
    "_incident_payload",
    "_incident_url",
    "_normalize_amount",
    "_normalize_chain",
    "_normalize_date",
    "_normalize_doc_headings",
    "_normalize_severity",
    "_recursive_char_split",
    "_seed_payload",
    "_seed_synthetic_url",
    "_split_at_headings",
    "_synthetic_incident_url",
    "_write_headers",
    "collection_names_for_category",
    "content_hash",
    "dedupe_incidents",
    "fetch_doc_source",
    "fetch_incident_source",
    "ingest_all",
    "ingest_collection",
    "ingest_doc_sources",
    "ingest_incidents_from_sources",
    "ingest_seed_corpus",
    "is_duplicate_content",
    "is_duplicate_url",
    "load_doc_sources",
    "load_incident_sources",
    "load_seed_corpus",
    "log",
    "main",
    "mark_content_ingested",
    "mark_url_ingested",
    "normalize_incident",
    "split_doc_into_chunks",
    "verify_ingestion",
]


if __name__ == "__main__":
    main()
