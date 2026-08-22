# VENDORED 2026-08-17 from rmi-backend/app/rag/ingest/_orchestrator.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""RAG ingest orchestration: bulk-ingest client, doc sources, CLI.

Extracted from ``app.rag.ingest_crypto_knowledge`` during the de-godding
split (2026-08-14). Owns the shared bulk-ingest HTTP client
(``ingest_collection``), the raw-doc source pipeline (``load_doc_sources`` /
``fetch_doc_source`` / ``ingest_doc_sources``), the curated knowledge-base
ingest (``ingest_all``), stats verification and the ``main`` CLI.

MONKEYPATCH CONTRACT: the pipeline tests patch
``app.rag.ingest_crypto_knowledge.is_duplicate_url`` / ``mark_url_ingested`` /
``is_duplicate_content`` / ``mark_content_ingested`` at runtime, so the dedup
names are resolved through the shim module object (``_ingest.X``) at call
time — never via ``from rugmunchbot._vendor.dedup import X``. Cross-section functions
(``load_incident_sources``, ``ingest_seed_corpus``,
``ingest_incidents_from_sources``) are likewise resolved through the shim to
keep the submodule import graph acyclic.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any

import httpx

import rugmunchbot._vendor.rag_pkg.ingest_crypto_knowledge as _ingest
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.config import (
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    RAG_MIN_CHUNK_CHARS,
)
from rugmunchbot._vendor.rag_pkg.ingest._chunking import _doc_payload, split_doc_into_chunks
from rugmunchbot._vendor.rag_pkg.ingest._knowledge_bases import KNOWLEDGE_BASES

log = get_logger("app.rag.ingest_crypto_knowledge")

# matches the /api/v1/rag/v2/bulk-ingest per-call limit
_BULK_CHUNK_SIZE = 500


# Bearer token kept for CSRF bypass; X-RMI-Key satisfies the auth middleware
# (Tier 1 shared secret) on the now-protected /api/v1/rag/v2/* write endpoints.
# Resolved lazily so this CLI module can be imported standalone (dry-runs,
# verification, unit tests) without the full backend env (JWT_SECRET guard in
# app.config). Write calls raise a clear error when the key is unavailable.
def _write_headers() -> dict[str, str]:
    try:
        from app.core.internal_key import INTERNAL_API_KEY
    except Exception as e:
        raise RuntimeError(
            "Unable to resolve INTERNAL_API_KEY — set RMI_INTERNAL_KEY (and JWT_SECRET) "
            "or run inside the backend container before writing to the RAG API."
        ) from e
    return {
        "Authorization": "Bearer sk-local",  # bypass CSRF middleware
        "X-RMI-Key": INTERNAL_API_KEY,  # auth middleware shared secret
        "Content-Type": "application/json",
    }


async def ingest_collection(
    base_url: str,
    collection: str,
    items: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    """Ingest all items into a collection via the bulk-ingest API."""
    if dry_run:
        return {"collection": collection, "items": len(items), "status": "dry_run"}

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.post(
                f"{base_url}/api/v1/rag/v2/bulk-ingest",
                json={"collection": collection, "items": items},
                headers=_write_headers(),
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            return {"collection": collection, "error": str(e)}


async def ingest_doc_sources(
    base_url: str = "http://localhost:8000",
    sources: list[str] | None = None,
    dry_run: bool = False,
    chunk_size: int = RAG_CHUNK_SIZE,
    chunk_overlap: int = RAG_CHUNK_OVERLAP,
    min_chunk_chars: int = RAG_MIN_CHUNK_CHARS,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch → chunk → dedup → ingest raw markdown/reST/AsciiDoc knowledge.

    Pipeline per enabled ``education`` source in crawl_sources.json:
      1. ``fetch_doc_source``       — httpx GET the raw doc (best-effort)
      2. URL dedup                  — dedup.is_duplicate_url (fail-open)
      3. ``split_doc_into_chunks``  — heading-aware chunking
      4. content dedup              — per-chunk dedup.content_hash + an
         in-batch hash set, plus dedup.is_duplicate_content per collection
         (both fail-open)
      5. bulk-ingest each configured collection via the existing
         ``ingest_collection`` path (embedding happens server-side, same as
         the incident loader)
      6. mark the URL + chunk content hashes ingested (fail-open)

    Fail-open contract: a source that 404s, times out, returns empty/parses to
    no usable chunks, or is already known is skipped and recorded in the
    summary — never raises.
    """
    configs = load_doc_sources()
    if sources is not None:
        wanted = set(sources)
        configs = [c for c in configs if c["name"] in wanted]
    enabled = [c for c in configs if c.get("enabled") is True]
    if not enabled:
        log.info("doc_no_enabled_sources")
        return {"enabled": 0, "reason": "no enabled education sources", "sources": {}}

    summary: dict[str, Any] = {"enabled": len(enabled), "sources": {}, "total_chunks": 0}
    for source_cfg in enabled:
        name = str(source_cfg["name"])
        url = str(source_cfg["url"])
        try:
            # Cheapest gate first: cross-time URL dedup (fail-open).
            # ``force`` bypasses the marks so re-ingestion is possible.
            if not force and _ingest.is_duplicate_url(url):
                summary["sources"][name] = {"url": url, "skipped": "url_already_ingested"}
                continue

            text = await fetch_doc_source(source_cfg)
            if text is None:
                summary["sources"][name] = {"url": url, "error": "fetch_failed"}
                continue

            chunks = split_doc_into_chunks(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                min_chunk_chars=min_chunk_chars,
            )
            if not chunks:
                summary["sources"][name] = {"url": url, "skipped": "no_usable_chunks"}
                continue

            collections = tuple(source_cfg.get("collections") or ())
            category = str(source_cfg.get("category") or "education")
            fresh: list[dict[str, Any]] = []
            seen_hashes: set[str] = set()
            for idx, chunk in enumerate(chunks):
                fp = _ingest.content_hash(chunk)
                if fp in seen_hashes:
                    continue
                # Cross-time content dedup per target collection (fail-open).
                if not force and any(_ingest.is_duplicate_content(fp, col) for col in collections):
                    continue
                seen_hashes.add(fp)
                fresh.append(_doc_payload(source=name, url=url, chunk=chunk, index=idx, category=category))

            ok_total = 0
            for col in collections:
                for start in range(0, len(fresh), _BULK_CHUNK_SIZE):
                    batch = fresh[start : start + _BULK_CHUNK_SIZE]
                    result = await ingest_collection(base_url, col, batch, dry_run=dry_run)
                    if not dry_run and "error" not in result:
                        ok_total += int(result.get("ok", 0) or 0)

            if not dry_run and fresh:
                _ingest.mark_url_ingested(url)
                for item in fresh:
                    fp = _ingest.content_hash(item["content"])
                    for col in collections:
                        _ingest.mark_content_ingested(fp, col)

            summary["sources"][name] = {
                "url": url,
                "chars": len(text),
                "chunks": len(chunks),
                "deduped": len(chunks) - len(fresh),
                "ingested": len(fresh) if dry_run else ok_total,
                "collections": list(collections),
            }
            summary["total_chunks"] += len(fresh)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("doc_source_failed", source=name, error=str(e))
            summary["sources"][name] = {"url": url, "error": str(e)}
    return summary


def load_doc_sources() -> list[dict[str, Any]]:
    """Load the ``education`` raw-doc source registry from crawl_sources.json.

    Reuses the validated incident-source loader (required fields + valid
    category/type) and keeps only the ``education`` category — the sources the
    raw markdown/reST/AsciiDoc ingest path is responsible for. Never raises.
    """
    return [s for s in _ingest.load_incident_sources() if s.get("category") == "education"]


async def fetch_doc_source(source_cfg: dict[str, Any]) -> str | None:
    """Fetch a raw markdown/reST/AsciiDoc file. Best-effort: None on failure."""
    url = str(source_cfg.get("url") or "")
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "RMI-RAG-Docs/1.0"})
            resp.raise_for_status()
            text = resp.text
    except httpx.HTTPError as e:
        log.warning("doc_fetch_failed", source=source_cfg.get("name"), url=url, error=str(e))
        return None
    if not text or not text.strip():
        log.warning("doc_fetch_empty", source=source_cfg.get("name"), url=url)
        return None
    return text


async def ingest_all(
    base_url: str = "http://localhost:8000",
    collections: list[str] | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Ingest all knowledge bases into their respective collections.

    Idempotent + resumable: every doc gets a stable synthetic URL
    (``https://kb.invalid/<collection>/<doc_id>``, RFC 2606 reserved TLD) plus
    a content hash. Docs whose URL or content was already ingested within the
    dedup TTL window are skipped and counted. ``--force`` bypasses the marks
    and re-ingests everything. Each item's metadata is augmented with source
    attribution (``source=curated_kb:<collection>``, ``ingested_via``,
    ``source_url``) so every vector in the corpus is traceable back to its
    knowledge base. Fail-open: dedup marks are best-effort (Redis down →
    proceed), never block ingestion.
    """
    cols = collections or list(KNOWLEDGE_BASES.keys())
    results: dict[str, Any] = {}
    total_skipped = 0

    for collection in cols:
        items = KNOWLEDGE_BASES.get(collection)
        if not items:
            results[collection] = {"status": "skipped", "reason": "no knowledge base defined"}
            continue

        fresh: list[dict[str, Any]] = []
        skipped = 0
        for item in items:
            doc_id = str(item.get("doc_id") or "")
            synthetic_url = f"https://kb.invalid/{collection}/{doc_id}"
            fp = _ingest.content_hash(str(item["content"]))
            if not force and (_ingest.is_duplicate_url(synthetic_url) or _ingest.is_duplicate_content(fp, collection)):
                skipped += 1
                continue
            meta = dict(item.get("metadata") or {})
            meta.setdefault("source", f"curated_kb:{collection}")
            meta.setdefault("source_name", "curated_knowledge_base")
            meta.setdefault("source_url", synthetic_url)
            meta.setdefault("ingested_via", "knowledge_base_loader")
            fresh.append({"doc_id": doc_id, "content": item["content"], "metadata": meta})
        total_skipped += skipped

        print(
            f"\n{'[DRY RUN] ' if dry_run else ''}Ingesting {len(fresh)} documents "
            f"into '{collection}' ({skipped} already-ingested skipped)..."
        )
        result = await ingest_collection(base_url, collection, fresh, dry_run=dry_run)
        if not dry_run and fresh:
            for payload in fresh:
                _ingest.mark_url_ingested(str(payload["metadata"].get("source_url") or ""))
                _ingest.mark_content_ingested(_ingest.content_hash(str(payload["content"])), collection)
        result["skipped_already_ingested"] = skipped
        results[collection] = result
        status = result.get("status", result.get("error", "unknown"))
        ok_count = result.get("ok", result.get("accepted", 0))
        print(f"  {collection}: {status} (ok={ok_count}, total={len(fresh)})")
        # Small delay between collections to avoid rate limiting.
        # ``asyncio.sleep`` keeps the event loop responsive (``time.sleep``
        # would block every other coroutine for the full second).
        if not dry_run:
            import asyncio

            await asyncio.sleep(1)

    results["_summary"] = {"total_skipped_already_ingested": total_skipped, "force": force}
    return results


async def verify_ingestion(base_url: str = "http://localhost:8000") -> dict[str, Any]:
    """Check RAG stats after ingestion."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{base_url}/api/v1/rag/v2/stats",
            headers={"Authorization": "Bearer sk-local"},
        )
        resp.raise_for_status()
        return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest crypto knowledge into RAG")
    parser.add_argument(
        "--base-url",
        default=os.getenv("RMI_API_URL", "http://localhost:8000"),
        help="RMI backend base URL",
    )
    parser.add_argument(
        "--collection",
        nargs="*",
        choices=[*list(KNOWLEDGE_BASES.keys()), "all"],
        default=["all"],
        help="Collections to populate (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be ingested without actually doing it",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Only verify current ingestion status",
    )
    parser.add_argument(
        "--incidents",
        action="store_true",
        help="Fetch + ingest public scam/rug/exploit incident sources (DefiLlama hacks, REKT, ...)",
    )
    parser.add_argument(
        "--docs",
        action="store_true",
        help="Fetch + ingest raw markdown/reST/AsciiDoc education sources (ConsenSys best practices, NotSoSmartContracts, ...)",
    )
    parser.add_argument(
        "--seed",
        nargs="?",
        const="default",
        default=None,
        metavar="SEED_FILE",
        help="Ingest the curated seed corpus (default: app/rag/seed_corpus/crypto_knowledge.jsonl; pass a path to override)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass dedup marks and re-ingest everything (knowledge bases, seed corpus, incidents, docs)",
    )
    args = parser.parse_args()

    import asyncio

    if args.verify:
        stats = asyncio.run(verify_ingestion(args.base_url))
        print("\n=== RAG Stats ===")
        print(json.dumps(stats, indent=2))
        return

    if args.incidents:
        results = asyncio.run(_ingest.ingest_incidents_from_sources(args.base_url, dry_run=args.dry_run))
        print("\n=== Incident Ingestion Results ===")
        print(json.dumps(results, indent=2))
        return

    if args.docs:
        results = asyncio.run(ingest_doc_sources(args.base_url, dry_run=args.dry_run, force=args.force))
        print("\n=== Doc Source Ingestion Results ===")
        print(json.dumps(results, indent=2))
        return

    if args.seed is not None:
        seed_file = None if args.seed == "default" else args.seed
        results = asyncio.run(
            _ingest.ingest_seed_corpus(args.base_url, seed_file=seed_file, dry_run=args.dry_run, force=args.force)
        )
        print("\n=== Seed Corpus Ingestion Results ===")
        print(json.dumps(results, indent=2))
        return

    collections = None if "all" in args.collection else args.collection

    if args.dry_run:
        total = sum(len(KNOWLEDGE_BASES.get(c, [])) for c in (collections or KNOWLEDGE_BASES))
        print(f"[DRY RUN] Would ingest {total} documents into {len(collections or KNOWLEDGE_BASES)} collections")
        # Show what would be sent
        for col_name, items in (collections or KNOWLEDGE_BASES).items():
            if isinstance(col_name, str) and col_name in KNOWLEDGE_BASES:
                print(f"\n  {col_name} ({len(items)} docs):")
                for item in items[:2]:
                    print(f"    - {item['doc_id']}")
                if len(items) > 2:
                    print(f"    ... and {len(items) - 2} more")
    else:
        results = asyncio.run(ingest_all(args.base_url, collections, dry_run=False, force=args.force))
        print("\n=== Ingestion Results ===")
        print(json.dumps(results, indent=2))

        # Verify
        print("\n=== Post-Ingestion Stats ===")
        stats = asyncio.run(verify_ingestion(args.base_url))
        print(json.dumps(stats, indent=2))
