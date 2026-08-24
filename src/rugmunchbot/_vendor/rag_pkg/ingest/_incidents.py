# VENDORED 2026-08-24 from rmi-backend/app/rag/ingest/_incidents.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Public scam/rug/exploit incident source registry, normalization + ingest.

Extracted from ``app.rag.ingest_crypto_knowledge`` during the de-godding
split (2026-08-14). Sources are declared in crawl_sources.json under
``incident_sources`` and consumed by ``ingest_incidents_from_sources``. Every
network touch is best-effort: failures log and return empty — never raise.

MONKEYPATCH CONTRACT: dedup names + ``ingest_collection`` are resolved through
the shim module object (``_ingest.X``) at call time so the pipeline tests'
runtime patches on ``app.rag.ingest_crypto_knowledge`` keep working.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx

import rugmunchbot._vendor.rag_pkg.ingest_crypto_knowledge as _ingest
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg._crawler_constants import (
    INCIDENT_CATEGORIES,
    INCIDENT_CATEGORY_BY_SOURCE,
    INCIDENT_COLLECTION_BY_CATEGORY,
    INCIDENT_SOURCES_FILE,
)
from rugmunchbot._vendor.rag_pkg.ingest._orchestrator import _BULK_CHUNK_SIZE, ingest_collection

log = get_logger("app.rag.ingest_crypto_knowledge")

_REQUIRED_SOURCE_FIELDS = ("name", "type", "url", "category", "collections", "enabled")
_VALID_SEVERITIES = ("critical", "high", "medium", "low", "informational")


def load_incident_sources() -> list[dict[str, Any]]:
    """Load the incident source registry from ``crawl_sources.json``.

    Returns only entries that carry the required fields (name, url, category,
    collections, enabled) with a valid category. Malformed entries are logged
    and skipped. Never raises.
    """
    try:
        data = json.loads(INCIDENT_SOURCES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("incident_sources_load_failed", path=str(INCIDENT_SOURCES_FILE), error=str(e))
        return []
    raw_sources = []
    if isinstance(data, dict):
        block = data.get("incident_sources")
        if isinstance(block, dict):
            raw_sources = block.get("sources", [])
    if not isinstance(raw_sources, list):
        return []

    sources: list[dict[str, Any]] = []
    for entry in raw_sources:
        if not isinstance(entry, dict):
            continue
        if any(f not in entry for f in _REQUIRED_SOURCE_FIELDS):
            log.warning("incident_source_missing_fields", source=entry.get("name"))
            continue
        if entry.get("category") not in INCIDENT_CATEGORIES:
            log.warning("incident_source_bad_category", source=entry.get("name"), category=entry.get("category"))
            continue
        if entry.get("type") not in ("json_api", "html_page"):
            log.warning("incident_source_bad_type", source=entry.get("name"), type=entry.get("type"))
            continue
        if not isinstance(entry.get("collections"), list) or not entry["collections"]:
            log.warning("incident_source_bad_collections", source=entry.get("name"))
            continue
        sources.append({k: entry[k] for k in _REQUIRED_SOURCE_FIELDS if k in entry})
    return sources


def normalize_incident(raw: dict[str, Any], source: str) -> dict[str, Any] | None:
    """Normalize a raw incident record into the canonical shape.

    Returns ``{title, description, chain, severity, category, amount_lost,
    date, source, url}`` or None when the record is malformed (not a dict or
    missing a usable title). Records without an explicit URL get a stable
    synthetic URL derived from (source, title) so URL-based dedup still works.
    Pure — no network, no I/O.
    """
    if not isinstance(raw, dict):
        return None
    raw_title = raw.get("title") or raw.get("name") or raw.get("project")
    if not isinstance(raw_title, str):
        return None
    title = raw_title.strip()
    if not title:
        return None

    description = str(
        raw.get("description")
        or raw.get("summary")
        or raw.get("details")
        or raw.get("technique")
        or raw.get("classification")
        or ""
    ).strip()
    category = raw.get("category")
    if category not in INCIDENT_CATEGORIES:
        category = INCIDENT_CATEGORY_BY_SOURCE.get(source, "scam")

    return {
        "title": title[:500],
        "description": description[:2000],
        "chain": _normalize_chain(raw.get("chain")),
        "severity": _normalize_severity(raw.get("severity")),
        "category": category,
        "amount_lost": _normalize_amount(
            raw.get("amount") or raw.get("amount_lost") or raw.get("losses") or raw.get("loss")
        ),
        "date": _normalize_date(raw.get("date")),
        "source": source,
        "url": _incident_url(raw, source, title),
    }


def dedupe_incidents(incidents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop incidents whose URL was already seen in the batch (first wins).

    Pure URL-dedup helper — the runtime ingestion path additionally consults
    the shared Redis-backed ``app.rag.dedup`` helpers (fail-open) so URLs
    ingested on previous runs are also skipped.
    """
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for incident in incidents:
        url = str(incident.get("url") or "").strip().rstrip("/")
        key = url.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(incident)
    return out


def collection_names_for_category(category: str) -> tuple[str, ...]:
    """Resolve the RAG collections for an incident category (rug/exploit/scam)."""
    return INCIDENT_COLLECTION_BY_CATEGORY.get(category, INCIDENT_COLLECTION_BY_CATEGORY["scam"])


def _incident_doc_id(url: str) -> str:
    """Stable document id for an incident URL."""
    return f"incident:{hashlib.sha256(url.encode()).hexdigest()[:12]}"


def _synthetic_incident_url(source: str, title: str) -> str:
    """Stable synthetic URL for records without one (dedup key only)."""
    digest = hashlib.sha256(f"{source}:{title}".encode()).hexdigest()[:12]
    return f"https://{source}/incident/{digest}"


def _incident_url(raw: dict[str, Any], source: str, title: str) -> str:
    for key in ("url", "link", "source_url"):
        value = raw.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            return value
    return _synthetic_incident_url(source, title)


def _normalize_chain(raw: Any) -> str:
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
        return ", ".join(dict.fromkeys(parts)) or "unknown"
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "unknown"


def _normalize_severity(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _VALID_SEVERITIES else "medium"


def _normalize_amount(raw: Any) -> int:
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)
    cleaned = str(raw or "").strip().replace(",", "").replace("$", "")
    if not cleaned:
        return 0
    try:
        return int(float(cleaned))
    except ValueError:
        return 0


def _normalize_date(raw: Any) -> str:
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC).isoformat()
        except (OSError, ValueError, OverflowError):
            return ""
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return ""
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return value[:64]
    return ""


def _incident_payload(incident: dict[str, Any]) -> dict[str, Any]:
    """Build the bulk-ingest item (doc_id/content/metadata) for a record."""
    url = str(incident["url"])
    content = (
        f"{incident['title']}. Chain: {incident['chain']}. "
        f"Amount lost: ${int(incident['amount_lost']):,}. Date: {incident['date']}. "
        f"{incident['description']}"
    ).strip()
    metadata = {
        "source": incident["source"],
        "source_url": url,
        "incident_url": url,
        "category": incident["category"],
        "severity": incident["severity"],
        "chain": incident["chain"],
        "amount_lost": incident["amount_lost"],
        "date": incident["date"],
        "published_at": incident["date"],
        "content_type": "incident",
        "ingested_via": "incident_loader",
    }
    return {"doc_id": _incident_doc_id(url), "content": content, "metadata": metadata}


async def fetch_incident_source(source_cfg: dict[str, Any], max_records: int | None = None) -> list[dict[str, Any]]:
    """Fetch a JSON incident feed. Best-effort: any failure returns [] (logged)."""
    url = str(source_cfg.get("url") or "")
    if not url:
        return []
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "RMI-RAG-Enrichment/1.0"})
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("incident_fetch_failed", source=source_cfg.get("name"), url=url, error=str(e))
        return []
    records = _extract_records(payload)
    if not isinstance(records, list):
        return []
    return records if max_records is None else records[:max_records]


def _extract_records(payload: Any) -> list[Any]:
    """Unwrap common API envelope shapes into a record list."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "hacks", "incidents", "results", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


async def ingest_incidents_from_sources(
    base_url: str = "http://localhost:8000",
    sources: list[str] | None = None,
    dry_run: bool = False,
    max_per_source: int | None = 1000,
    force: bool = False,
) -> dict[str, Any]:
    """Fetch → normalize → dedup → ingest public scam/exploit incident feeds.

    Pipeline per enabled ``json_api`` source in crawl_sources.json:
      1. ``fetch_incident_source``          — httpx GET (best-effort, [] on failure)
      2. ``normalize_incident``             — canonical record shape, drop invalid
      3. ``dedupe_incidents`` + dedup.is_duplicate_url — unique by URL (in-batch
         plus the shared Redis URL dedup, fail-open; ``force=True`` bypasses
         the cross-time URL dedup)
      4. map category → collection(s), bulk-ingest via the existing
         ``ingest_collection`` path (chunked at 500 items).

    Never raises: a failing source contributes an error entry to the summary.
    """
    configs = load_incident_sources()
    if sources is not None:
        wanted = set(sources)
        configs = [c for c in configs if c["name"] in wanted]
    enabled = [c for c in configs if c.get("enabled") is True and c.get("type") == "json_api"]
    if not enabled:
        log.info("incident_no_enabled_sources")
        return {"enabled": 0, "reason": "no enabled json_api sources", "sources": {}}

    summary: dict[str, Any] = {"enabled": len(enabled), "sources": {}, "total_normalized": 0}
    for source_cfg in enabled:
        name = str(source_cfg["name"])
        try:
            raw_records = await fetch_incident_source(source_cfg, max_records=max_per_source)
            normalized = [n for r in raw_records if (n := normalize_incident(r, name)) is not None]
            batch = dedupe_incidents(normalized)

            # Cross-time URL dedup via the shared dedup module (fail-open).
            # ``force`` bypasses the marks so re-ingestion is possible.
            fresh: list[dict[str, Any]] = []
            for incident in batch:
                if not force and _ingest.is_duplicate_url(str(incident["url"])):
                    continue
                fresh.append(incident)

            collections = tuple(
                source_cfg.get("collections") or collection_names_for_category(str(source_cfg["category"]))
            )
            per_collection: dict[str, list[dict[str, Any]]] = {col: [] for col in collections}
            for incident in fresh:
                payload = _incident_payload(incident)
                for col in collections:
                    per_collection[col].append(payload)

            ok_total = 0
            for col, items in per_collection.items():
                for start in range(0, len(items), _BULK_CHUNK_SIZE):
                    chunk = items[start : start + _BULK_CHUNK_SIZE]
                    result = await ingest_collection(base_url, col, chunk, dry_run=dry_run)
                    if not dry_run and "error" not in result:
                        ok_total += int(result.get("ok", 0) or 0)
            if not dry_run:
                for incident in fresh:
                    _ingest.mark_url_ingested(str(incident["url"]))

            summary["sources"][name] = {
                "fetched": len(raw_records),
                "normalized": len(normalized),
                "deduped_in_batch": len(normalized) - len(batch),
                "skipped_already_ingested": len(batch) - len(fresh),
                "ingested": len(fresh) if dry_run else ok_total,
                "collections": list(collections),
            }
            summary["total_normalized"] += len(fresh)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("incident_source_failed", source=name, error=str(e))
            summary["sources"][name] = {"error": str(e)}
    return summary
