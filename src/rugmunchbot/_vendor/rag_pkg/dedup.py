# VENDORED 2026-08-17 from rmi-backend/app/rag/dedup.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
# DUPLICATE ELIMINATED 2026-08-22: this file was a byte-identical copy of
# ``rugmunchbot._vendor.dedup`` (~800 lines). The runtime dedup namespace is
# ``_vendor/dedup.py`` (crawler, ingest_crypto_knowledge and ratings import
# from it directly); nothing imports this module statically, but the shim is
# kept so dynamic/string-based resolutions of
# ``rugmunchbot._vendor.rag_pkg.dedup`` keep working after vendor refreshes.
"""Re-export shim — canonical implementation lives in ``_vendor/dedup.py``."""

from rugmunchbot._vendor.dedup import (
    KNOWN_PEOPLE,
    KNOWN_PROTOCOLS,
    KNOWN_TOKENS,
    DedupResult,
    _get_redis as _get_redis,
    _reset_redis_client_for_tests as _reset_redis_client_for_tests,
    check_duplicates,
    content_fingerprint,
    content_hash,
    doc_id_hash,
    extract_crypto_entities,
    is_duplicate_by_entities,
    is_duplicate_by_title,
    is_duplicate_content,
    is_duplicate_url,
    is_valid_evm_address,
    lookup_group_by_fp,
    lookup_group_by_url,
    mark_content_ingested,
    mark_url_ingested,
    near_duplicate,
    new_group_id,
    normalize_address,
    normalize_text,
    record_fp_group,
    record_group_membership,
    to_checksum_address,
    tokenize,
    url_hash,
)

__all__ = [
    "KNOWN_PEOPLE",
    "KNOWN_PROTOCOLS",
    "KNOWN_TOKENS",
    "DedupResult",
    "check_duplicates",
    "content_fingerprint",
    "content_hash",
    "doc_id_hash",
    "extract_crypto_entities",
    "is_duplicate_by_entities",
    "is_duplicate_by_title",
    "is_duplicate_content",
    "is_duplicate_url",
    "is_valid_evm_address",
    "lookup_group_by_fp",
    "lookup_group_by_url",
    "mark_content_ingested",
    "mark_url_ingested",
    "near_duplicate",
    "new_group_id",
    "normalize_address",
    "normalize_text",
    "record_fp_group",
    "record_group_membership",
    "to_checksum_address",
    "tokenize",
    "url_hash",
]
