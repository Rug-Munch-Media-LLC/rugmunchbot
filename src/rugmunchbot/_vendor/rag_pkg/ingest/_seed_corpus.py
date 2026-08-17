# VENDORED 2026-08-17 from rmi-backend/app/rag/ingest/_seed_corpus.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Curated JSONL seed corpus loader + ingest.

Extracted from ``app.rag.ingest_crypto_knowledge`` during the de-godding
split (2026-08-14). Loads ``app/rag/seed_corpus/crypto_knowledge.jsonl`` —
hand-written educational entries (definitions, scam patterns, TA terms,
market concepts, security best practices). Every entry: {id, title, content,
category, source, tags}. ``source`` is forced to "seed-corpus" at load time
so attribution stays consistent regardless of what the file says.

MONKEYPATCH CONTRACT: dedup names + ``ingest_collection`` are resolved through
the shim module object (``_ingest.X``) at call time so the pipeline tests'
runtime patches on ``app.rag.ingest_crypto_knowledge`` keep working.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import rugmunchbot._vendor.rag_pkg.ingest_crypto_knowledge as _ingest
from rugmunchbot._vendor.rag_pkg.ingest._orchestrator import _BULK_CHUNK_SIZE, ingest_collection

# app/rag/seed_corpus/crypto_knowledge.jsonl — lives one directory up from
# this package (app/rag/seed_corpus/), hence the extra ``.parent``.
SEED_CORPUS_DEFAULT_PATH: Path = Path(__file__).resolve().parent.parent / "seed_corpus" / "crypto_knowledge.jsonl"

# category → legacy RAG collection. Concepts/terminology land in
# ``phishing_db`` (DeFi concepts, terminology, security concepts per the
# module docstring), scam patterns in ``scam_intel``, security best practices
# in ``contract_audit``, TA terms in ``wallet_labels``.
SEED_CATEGORY_COLLECTIONS: dict[str, str] = {
    "definition": "phishing_db",
    "defi": "phishing_db",
    "market": "phishing_db",
    "tokenomics": "phishing_db",
    "onchain": "phishing_db",
    "scam": "scam_intel",
    "security": "contract_audit",
    "ta": "wallet_labels",
}

_SEED_REQUIRED_FIELDS = ("id", "title", "content", "category")


def load_seed_corpus(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse + validate the seed-corpus JSONL file. Pure — no network, no I/O
    beyond reading *path*. Never raises.

    Returns ``(entries, errors)`` where every entry is a validated dict
    ``{id, title, content, category, source, tags}``. ``source`` is forced to
    ``"seed-corpus"``. Malformed lines / missing fields / duplicate ids are
    reported as error strings and skipped.
    """
    if not path.is_file():
        return [], [f"seed_file_missing path={path}"]
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return [], [f"seed_file_unreadable path={path} err={e}"]
    for lineno, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # allow blank lines + comments
        try:
            raw = json.loads(stripped)
        except json.JSONDecodeError as e:
            errors.append(f"line {lineno}: invalid JSON ({e})")
            continue
        if not isinstance(raw, dict):
            errors.append(f"line {lineno}: entry is not a JSON object")
            continue
        missing = [f for f in _SEED_REQUIRED_FIELDS if not raw.get(f)]
        if missing:
            errors.append(f"line {lineno}: missing required field(s) {', '.join(missing)}")
            continue
        entry_id = str(raw["id"]).strip()
        title = str(raw["title"]).strip()
        content = str(raw["content"]).strip()
        category = str(raw["category"]).strip()
        if not entry_id or not title or not content or not category:
            errors.append(f"line {lineno}: empty required field(s)")
            continue
        if entry_id in seen_ids:
            errors.append(f"line {lineno}: duplicate id {entry_id!r}")
            continue
        if category not in SEED_CATEGORY_COLLECTIONS:
            errors.append(
                f"line {lineno}: unknown category {category!r} (allowed: {sorted(SEED_CATEGORY_COLLECTIONS)})"
            )
            continue
        seen_ids.add(entry_id)
        tags = raw.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            errors.append(f"line {lineno}: tags must be a list of strings")
            continue
        entries.append(
            {
                "id": entry_id,
                "title": title,
                "content": content,
                "category": category,
                "source": "seed-corpus",
                "tags": tags,
            }
        )
    return entries, errors


def _seed_synthetic_url(entry_id: str) -> str:
    """Stable, non-resolvable dedup URL for a seed entry.

    ``.invalid`` is a reserved TLD (RFC 2606) — never resolves, never routes,
    and keeps the corpus OSS-safe (no internal hostnames).
    """
    return f"https://seed-corpus.invalid/{entry_id}"


def _seed_payload(entry: dict[str, Any], collection: str) -> dict[str, Any]:
    """Build the bulk-ingest item (doc_id/content/metadata) for a seed entry."""
    synthetic_url = _seed_synthetic_url(str(entry["id"]))
    digest = hashlib.sha256(str(entry["id"]).encode("utf-8", errors="ignore")).hexdigest()[:12]
    metadata = {
        "source": "seed-corpus",
        "source_name": "seed-corpus",
        "source_url": synthetic_url,
        "title": entry["title"],
        "category": entry["category"],
        "tags": list(entry.get("tags") or []),
        "content_type": "seed_knowledge",
        "ingested_via": "seed_corpus_loader",
    }
    return {
        "doc_id": f"seed:{digest}",
        "content": f"{entry['title']}\n\n{entry['content']}",
        "metadata": metadata,
    }


async def ingest_seed_corpus(
    base_url: str = "http://localhost:8000",
    seed_file: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Load → validate → dedup → ingest the curated seed corpus.

    Pipeline:
      1. ``load_seed_corpus``      — parse + validate JSONL (malformed skipped)
      2. URL dedup                 — dedup.is_duplicate_url on the stable
         synthetic URL per entry (fail-open)
      3. content dedup             — dedup.content_hash + is_duplicate_content
         per target collection (fail-open)
      4. map category → collection, bulk-ingest via ``ingest_collection``
         (chunked at _BULK_CHUNK_SIZE; embedding happens server-side)
      5. mark URL + content hashes ingested (fail-open, skipped on dry-run)

    ``--force`` bypasses the dedup marks and re-ingests every entry.
    Never raises: failures are collected in the summary.
    """
    path = Path(seed_file) if seed_file is not None else SEED_CORPUS_DEFAULT_PATH
    entries, errors = load_seed_corpus(path)
    summary: dict[str, Any] = {
        "seed_file": str(path),
        "total_entries": len(entries),
        "invalid_entries": len(errors),
        "errors": errors[:20],
        "skipped_url_duplicates": 0,
        "skipped_content_duplicates": 0,
        "force": force,
        "collections": {},
        "to_ingest": 0,
        "ingested": 0,
    }
    if not entries:
        return summary

    payloads_by_collection: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        collection = SEED_CATEGORY_COLLECTIONS.get(str(entry["category"]), "phishing_db")
        payload = _seed_payload(entry, collection)
        synthetic_url = str(payload["metadata"]["source_url"])
        fp = _ingest.content_hash(payload["content"])
        if not force and _ingest.is_duplicate_url(synthetic_url):
            summary["skipped_url_duplicates"] += 1
            continue
        if not force and _ingest.is_duplicate_content(fp, collection):
            summary["skipped_content_duplicates"] += 1
            continue
        payloads_by_collection.setdefault(collection, []).append(payload)

    summary["to_ingest"] = sum(len(v) for v in payloads_by_collection.values())
    ok_total = 0
    for collection, items in payloads_by_collection.items():
        for start in range(0, len(items), _BULK_CHUNK_SIZE):
            chunk = items[start : start + _BULK_CHUNK_SIZE]
            result = await ingest_collection(base_url, collection, chunk, dry_run=dry_run)
            if not dry_run and "error" not in result:
                ok_total += int(result.get("ok", 0) or 0)
        summary["collections"][collection] = len(items)

    if not dry_run:
        for collection, items in payloads_by_collection.items():
            for payload in items:
                _ingest.mark_url_ingested(str(payload["metadata"].get("source_url") or ""))
                _ingest.mark_content_ingested(_ingest.content_hash(str(payload["content"])), collection)
    summary["ingested"] = summary["to_ingest"] if dry_run else ok_total
    return summary
