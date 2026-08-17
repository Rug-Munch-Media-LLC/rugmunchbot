# VENDORED 2026-08-17 from rmi-backend/app/rag/scan_memory.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Scan-to-RAG memory — auto-index token scan + wallet trust summaries.

Every RugMunchBot token scan (``scan_token`` / ``scan_token_v2`` /
sentinel deep scan) writes a compact summary into the Qdrant collection
``scanned_tokens`` so future chat queries can recall "have we scanned
this token before?" with full risk context.

Wallet scans (``/wallet``) write their trust label into the sibling
``scanned_wallets`` collection via ``index_wallet_memory`` /
``schedule_wallet_memory_index`` — the labeling system accumulates "we
see them a lot": every re-scan upserts the wallet's current trust score,
funding label and rug association on a deterministic per-day point ID.

Design (mirrors ``app.domains.scanner.whale_indexer`` — the other
scanner-domain Qdrant writer in this repo):

  - Collections: ``scanned_tokens`` / ``scanned_wallets``, bare 1024-dim
    Cosine vectors (bge-m3 is the primary embedder; dimension comes from
    ``EMBEDDING_DIM``).
  - Deterministic point ID: ``uuid5(URL, "scan:{chain}:{address}:{date}")``
    (tokens) / ``uuid5(URL, "wallet:{chain}:{address}:{date}")`` (wallets)
    so re-scans on the same calendar day upsert (overwrite) the same
    point instead of growing unbounded.
  - Fail-open: any failure (Qdrant down, embedder down, malformed scan
    result) logs a warning and returns ``{"indexed": False}`` — it never
    raises into the scan pipeline and never blocks the scan response.
  - Callers fire-and-forget via ``schedule_scan_memory_index`` /
    ``schedule_wallet_memory_index`` (background tasks with a strong
    reference) so a scan reply is never delayed by RAG ingestion.

Recall side: both collections are searchable through the RAG engine's
Qdrant fallback (``app.rag.engine._qdrant_search``) once a caller
includes them in its collection list (see the ``COLLECTIONS`` default in
``app.rag.models``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from rugmunchbot._vendor.settings_shim import settings
from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.rag_pkg.embeddings import EMBEDDING_DIM, get_embedding

log = get_logger(__name__)

# ── Collection configuration ───────────────────────────────────────────
COLLECTION = "scanned_tokens"
WALLET_COLLECTION = "scanned_wallets"

# All existing Qdrant collections in this repo are 1024-dim Cosine
# (the primary embedder is bge-m3). Dimension derived, not hardcoded,
# so it tracks the active embedder model.
VECTOR_SIZE = int(EMBEDDING_DIM)
DISTANCE = "Cosine"

# Resolved via app.config (env var wins; Docker-compose name inside the
# network; host-mapped localhost on host-side systemd runs).
_QDRANT_URL = settings.qdrant_url.rstrip("/")

# Collection created once per process (mirrors the _COLLECTION_ENSURED
# pattern in app/domains/scanner/whale_indexer.py).
_COLLECTION_ENSURED = False
_WALLET_COLLECTION_ENSURED = False

# Strong references for background index tasks — keeps them from being
# garbage-collected mid-flight in CPython's asyncio.
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()

# Scan timestamp formats accepted by extract_scan_summary
_SOURCE_LABELS = {
    "scan_v1": "scan_v1",
    "scan_v2": "scan_v2",
    "sentinel": "sentinel",
}


def _short_label(address: str) -> str:
    """Compact address label for scan results without a token name."""
    addr = address or ""
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"


# ── Summary extraction (legacy / v2 / sentinel shapes) ────────────────


def extract_scan_summary(result: dict[str, Any], *, source: str = "scan_v1") -> dict[str, Any]:
    """Normalize any scan result shape into the scan-memory summary dict.

    Handles the three shapes the bot scanner returns:

      - ``scan_v1`` — legacy multi-source dict (``scan_token`` result;
        also ``v2_token_to_legacy`` output which is a superset of it).
      - ``scan_v2`` — raw ``POST /api/v2/scanner/token/scan`` payload
        (``safety_score`` 0-100, higher = safer → ``risk = 100 - safety``).
      - ``sentinel`` — SENTINEL deep forensic report (``composite_risk_score``,
        ``holder_analysis.total_holders``, ``red_flags``).

    Returns the summary dict; unknown/missing fields default to
    falsy/neutral values so the doc builder is always safe.
    """
    result = result or {}
    ts = str(result.get("scanned_at") or result.get("timestamp") or datetime.now(UTC).isoformat())
    chain = str(result.get("chain") or "unknown")
    address = str(result.get("address") or result.get("token_address") or result.get("token") or "")

    if source == "scan_v2":
        safety = float(result.get("safety_score") or 0)
        risk_score = round(100 - min(max(safety, 0.0), 100.0), 1)
        risk_level = str(result.get("risk_level") or "")
        flags = [str(f).lower() for f in (result.get("risk_flags") or [])]
        honeypot: bool | None = any("honeypot" in f for f in flags) or None
        evidence = result.get("evidence") or {}
        holders_data = evidence.get("holders") or {}
        holders = int(holders_data.get("holder_count") or 0)
        liquidity = float(result.get("liquidity_usd") or 0)
        name = str(result.get("name") or "Unknown")
        symbol = str(result.get("symbol") or "???")
        threats = [str(t) for t in (result.get("risk_flags") or [])]
    elif source == "sentinel":
        risk_score = float(result.get("composite_risk_score") or 0)
        risk_level = str(result.get("risk_level") or "unknown")
        flags = [str(f).lower() for f in (result.get("red_flags") or [])]
        honeypot = any("honeypot" in f for f in flags) or None
        holder_analysis = result.get("holder_analysis") or {}
        holders = int(holder_analysis.get("total_holders") or 0)
        liquidity = float(result.get("liquidity_usd") or 0)
        name = str(result.get("name") or _short_label(address))
        symbol = str(result.get("symbol") or "")
        threats = [str(t) for t in (result.get("red_flags") or [])]
    else:  # scan_v1 legacy dict
        risk_score = float(result.get("risk_score") or 0)
        risk_level = str(result.get("risk_level") or "")
        honeypot = result.get("honeypot")
        holders = int(result.get("holders") or 0)
        liquidity = float(result.get("liquidity") or 0)
        name = str(result.get("name") or "Unknown")
        symbol = str(result.get("symbol") or "???")
        threats = [str(t) for t in (result.get("threats") or [])]

    return {
        "name": name,
        "symbol": symbol,
        "address": address,
        "chain": chain,
        "risk_score": risk_score,
        "risk_max": 100,
        "risk_level": risk_level,
        "honeypot": honeypot,
        "liquidity": liquidity,
        "holders": holders,
        "threats": threats,
        "timestamp": ts,
        "source": _SOURCE_LABELS.get(source, source),
    }


def should_index_scan(result: dict[str, Any], *, source: str = "scan_v1") -> bool:
    """Gate: only index scans that actually resolved token data.

    Token-not-found / empty scans (``name == "Unknown"`` with no
    enrichment) would pollute the memory with "Unknown" docs. Sentinel
    only returns a dict on a completed forensic scan, so it always
    qualifies.
    """
    result = result or {}
    if source == "sentinel":
        return bool(
            result.get("composite_risk_score") is not None or result.get("token_address") or result.get("address")
        )
    name = str(result.get("name") or "")
    return bool(name and name != "Unknown")


# ── Doc builder ────────────────────────────────────────────────────────


def build_scan_doc(summary: dict[str, Any]) -> str:
    """Build the compact semantic doc indexed into Qdrant.

    Format::

        Token {name} ({address}) on {chain}: risk_score={score}/{max},
        honeypot={bool}, liquidity=${X}, holders={N}. Scan at {timestamp}.
    """
    risk = float(summary.get("risk_score") or 0)
    risk_max = int(summary.get("risk_max") or 100)
    hp = "true" if summary.get("honeypot") else "false"
    liq = float(summary.get("liquidity") or 0)
    holders = int(summary.get("holders") or 0)
    return (
        f"Token {summary.get('name') or 'Unknown'} ({summary.get('address')}) "
        f"on {summary.get('chain') or 'unknown'}: "
        f"risk_score={risk:.0f}/{risk_max}, honeypot={hp}, "
        f"liquidity=${liq:,.0f}, holders={holders:,}. "
        f"Scan at {summary.get('timestamp')}."
    )


# ── Point IDs ──────────────────────────────────────────────────────────


def scan_point_id(chain: str, address: str, timestamp: str) -> str:
    """Deterministic Qdrant point UUID for a scan.

    Keyed on ``scan:{chain}:{address}:{YYYY-MM-DD}`` (the date prefix
    ``timestamp[:10]``) so re-scans on the same day overwrite the same
    point — the memory stays bounded and current.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"scan:{chain}:{address}:{timestamp[:10]}"))


# ── Qdrant helpers ─────────────────────────────────────────────────────


async def ensure_collection(client: httpx.AsyncClient, collection: str = COLLECTION) -> bool:
    """Create a scan-memory collection once per process if missing.

    ``collection`` defaults to ``COLLECTION`` (``scanned_tokens``) for
    backward compatibility; the wallet feed passes ``WALLET_COLLECTION``.

    Fail-open: any probe/creation failure returns False (the caller drops
    the index with a warning) — never raises into the scan pipeline.
    """
    global _COLLECTION_ENSURED
    global _WALLET_COLLECTION_ENSURED
    if _COLLECTION_ENSURED and collection == COLLECTION:
        return True
    if collection == WALLET_COLLECTION and _WALLET_COLLECTION_ENSURED:
        return True
    try:
        r = await client.get(f"/collections/{collection}")
        if r.status_code == 404:
            created = await client.put(
                f"/collections/{collection}",
                json={
                    "vectors": {
                        "size": VECTOR_SIZE,
                        "distance": DISTANCE,
                    }
                },
            )
            if created.status_code not in (200, 201):
                log.warning(
                    "scan_memory_collection_create_failed name=%s status=%d body=%s",
                    collection,
                    created.status_code,
                    created.text[:200],
                )
                return False
            log.info("scan_memory_collection_created name=%s size=%d", collection, VECTOR_SIZE)
        elif r.status_code != 200:
            log.warning("scan_memory_collection_probe_failed name=%s status=%d", collection, r.status_code)
            return False
    except (httpx.HTTPError, httpx.TimeoutException, ValueError) as exc:
        log.warning("scan_memory_collection_unreachable name=%s error=%s", collection, exc)
        return False
    if collection == WALLET_COLLECTION:
        _WALLET_COLLECTION_ENSURED = True
    else:
        _COLLECTION_ENSURED = True
    return True


async def index_scan_memory(
    result: dict[str, Any],
    *,
    source: str = "scan_v1",
    client: httpx.AsyncClient | None = None,
    vector: list[float] | None = None,
) -> dict[str, Any]:
    """Embed + upsert one scan summary into ``scanned_tokens``.

    Fail-open: any failure (Qdrant down, embedder down, malformed scan
    result) logs a warning and returns ``{"indexed": False}`` — never
    raises. Callers fire-and-forget via ``schedule_scan_memory_index``.

    ``client`` and ``vector`` are test seams; production callers omit them.
    """
    try:
        summary = extract_scan_summary(result, source=source)
        address = summary.get("address") or ""
        if not address:
            return {"indexed": False, "error": "missing address"}
        doc = build_scan_doc(summary)
        if vector is None:
            vector = await get_embedding(doc)
        if len(vector) != VECTOR_SIZE:
            log.warning(
                "scan_memory_dim_mismatch got=%d want=%d",
                len(vector),
                VECTOR_SIZE,
            )
            return {"indexed": False, "error": f"vector dim {len(vector)} != {VECTOR_SIZE}"}

        pid = scan_point_id(summary.get("chain", "unknown"), address, summary.get("timestamp", ""))
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(base_url=_QDRANT_URL, timeout=5.0)
        try:
            if not await ensure_collection(client):
                return {"indexed": False, "error": "collection unavailable"}
            body: dict[str, Any] = {
                "points": [
                    {
                        "id": pid,
                        "vector": vector,
                        "payload": {
                            "content": doc,
                            "metadata": summary,
                            "source": f"scan:{source}",
                            "chain": summary.get("chain", "unknown"),
                            "collection": COLLECTION,
                        },
                    }
                ]
            }
            r = await client.put(f"/collections/{COLLECTION}/points", json=body)
            if r.status_code not in (200, 201):
                log.warning(
                    "scan_memory_upsert_failed status=%d body=%s",
                    r.status_code,
                    r.text[:200],
                )
                return {"indexed": False, "error": f"qdrant status {r.status_code}"}
        finally:
            if owns_client:
                await client.aclose()
        return {"indexed": True, "collection": COLLECTION, "point_id": pid, "doc": doc}
    except Exception as exc:  # fail-open contract (see module docstring)
        log.warning("scan_memory_index_failed error=%s", exc)
        return {"indexed": False, "error": str(exc)}


def schedule_scan_memory_index(result: dict[str, Any], *, source: str = "scan_v1") -> asyncio.Task[Any] | None:
    """Fire-and-forget a scan-memory index in the background.

    Returns the Task (for tests / observability) or None when no event
    loop is running. Never raises — ingestion failure never blocks the
    scan response.
    """
    coro = index_scan_memory(result, source=source)
    try:
        task = asyncio.create_task(coro)
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return task
    except RuntimeError as exc:  # no running event loop
        coro.close()  # avoid an un-awaited coroutine warning
        log.debug("scan_memory_schedule_no_loop err=%s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# WALLET TRUST MEMORY — the labeling system accumulates over time
# ═══════════════════════════════════════════════════════════════════════════
# ``/wallet`` scans write their computed trust label into ``scanned_wallets``
# so the RAG layer learns "we see them a lot": deterministic per-day point
# IDs keep the memory bounded (a wallet re-scanned today overwrites
# yesterday's point) while accumulating the history of every labeled wallet.


def extract_wallet_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize a wallet scan result (+ nested ``trust`` block) for RAG.

    Reads the ``trust`` payload produced by the own-wallet-trust engine
    (``app.domains.scanner.engines.own_wallet_trust.get_wallet_trust``)
    plus the wallet identity/risk fields from the scan result. Missing
    fields default to neutral values so the doc builder is always safe.
    """
    result = result or {}
    _raw_trust = result.get("trust")
    trust = _raw_trust if isinstance(_raw_trust, dict) else {}
    ts = str(result.get("scanned_at") or result.get("timestamp") or datetime.now(UTC).isoformat())
    chain = str(result.get("chain") or "unknown")
    address = str(result.get("address") or result.get("wallet") or "")
    entity = str(result.get("entity_name") or "")
    entity_type = str(result.get("entity_type") or "")
    risk = float(result.get("risk_score") or 0)
    trust_score = trust.get("trust_score")
    return {
        "address": address,
        "chain": chain,
        "trust_score": int(trust_score) if trust_score is not None else None,
        "trust_level": str(trust.get("trust_level") or ""),
        "verdict": str(trust.get("trust_line") or ""),
        "signals": [str(s) for s in (trust.get("signals") or [])],
        "entity_name": entity,
        "entity_type": entity_type,
        "funding_source": str((trust.get("factors") or {}).get("funding_source") or ""),
        "rug_count": (trust.get("factors") or {}).get("rug_count"),
        "risk_score": risk,
        "labels": [str(lab) for lab in (result.get("labels") or [])],
        "timestamp": ts,
        "source": "wallet_scan",
    }


def should_index_wallet(result: dict[str, Any]) -> bool:
    """Gate: only index wallets that actually have an address (and trust or risk)."""
    result = result or {}
    _raw_trust = result.get("trust")
    trust = _raw_trust if isinstance(_raw_trust, dict) else {}
    address = str(result.get("address") or result.get("wallet") or "")
    return bool(address) and (trust.get("trust_score") is not None or result.get("risk_score") is not None)


def build_wallet_doc(summary: dict[str, Any]) -> str:
    """Build the compact semantic doc indexed into Qdrant.

    Format::

        Wallet {address} on {chain}: trust_score={X}/100, trust_level={level},
        verdict="{...}", funding={src}, rugs={N}, entity={name}. Scan at {ts}.
    """
    trust = summary.get("trust_score")
    trust_str = f"{int(trust)}/100" if trust is not None else "n/a"
    level = summary.get("trust_level") or "unknown"
    verdict = summary.get("verdict") or ""
    funding = summary.get("funding_source") or "unknown"
    rugs = summary.get("rug_count")
    rugs_str = str(rugs) if rugs is not None else "n/a"
    entity = summary.get("entity_name") or "unknown"
    return (
        f"Wallet {summary.get('address') or 'unknown'} "
        f"on {summary.get('chain') or 'unknown'}: "
        f'trust_score={trust_str}, trust_level={level}, verdict="{verdict}", '
        f"funding={funding}, prior_rugs={rugs_str}, entity={entity}. "
        f"Scan at {summary.get('timestamp')}."
    )


def wallet_point_id(chain: str, address: str, timestamp: str) -> str:
    """Deterministic Qdrant point UUID for a wallet scan.

    Keyed on ``wallet:{chain}:{address}:{YYYY-MM-DD}`` so re-scans on the
    same day overwrite the same point — the labeling system stays bounded
    and current while accumulating across days.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"wallet:{chain}:{address}:{timestamp[:10]}"))


async def index_wallet_memory(
    result: dict[str, Any],
    *,
    client: httpx.AsyncClient | None = None,
    vector: list[float] | None = None,
) -> dict[str, Any]:
    """Embed + upsert one wallet trust summary into ``scanned_wallets``.

    Fail-open: any failure (Qdrant down, embedder down, malformed wallet
    result) logs a warning and returns ``{"indexed": False}`` — never
    raises. Callers fire-and-forget via ``schedule_wallet_memory_index``.
    """
    try:
        summary = extract_wallet_summary(result)
        address = summary.get("address") or ""
        if not address:
            return {"indexed": False, "error": "missing address"}
        if not should_index_wallet(result):
            return {"indexed": False, "error": "no trust/risk data"}
        doc = build_wallet_doc(summary)
        if vector is None:
            vector = await get_embedding(doc)
        if len(vector) != VECTOR_SIZE:
            log.warning(
                "wallet_memory_dim_mismatch got=%d want=%d",
                len(vector),
                VECTOR_SIZE,
            )
            return {"indexed": False, "error": f"vector dim {len(vector)} != {VECTOR_SIZE}"}

        pid = wallet_point_id(summary.get("chain", "unknown"), address, summary.get("timestamp", ""))
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(base_url=_QDRANT_URL, timeout=5.0)
        try:
            if not await ensure_collection(client, WALLET_COLLECTION):
                return {"indexed": False, "error": "collection unavailable"}
            body: dict[str, Any] = {
                "points": [
                    {
                        "id": pid,
                        "vector": vector,
                        "payload": {
                            "content": doc,
                            "metadata": summary,
                            "source": "scan:wallet",
                            "chain": summary.get("chain", "unknown"),
                            "collection": WALLET_COLLECTION,
                        },
                    }
                ]
            }
            r = await client.put(f"/collections/{WALLET_COLLECTION}/points", json=body)
            if r.status_code not in (200, 201):
                log.warning(
                    "wallet_memory_upsert_failed status=%d body=%s",
                    r.status_code,
                    r.text[:200],
                )
                return {"indexed": False, "error": f"qdrant status {r.status_code}"}
        finally:
            if owns_client:
                await client.aclose()
        return {"indexed": True, "collection": WALLET_COLLECTION, "point_id": pid, "doc": doc}
    except Exception as exc:  # fail-open contract (see module docstring)
        log.warning("wallet_memory_index_failed error=%s", exc)
        return {"indexed": False, "error": str(exc)}


def schedule_wallet_memory_index(result: dict[str, Any]) -> asyncio.Task[Any] | None:
    """Fire-and-forget a wallet trust memory index in the background.

    Returns the Task (for tests / observability) or None when no event
    loop is running. Never raises — ingestion failure never blocks the
    /wallet response.
    """
    coro = index_wallet_memory(result)
    try:
        task = asyncio.create_task(coro)
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return task
    except RuntimeError as exc:  # no running event loop
        coro.close()  # avoid an un-awaited coroutine warning
        log.debug("wallet_memory_schedule_no_loop err=%s", exc)
        return None


__all__ = [
    "COLLECTION",
    "DISTANCE",
    "VECTOR_SIZE",
    "WALLET_COLLECTION",
    "build_scan_doc",
    "build_wallet_doc",
    "ensure_collection",
    "extract_scan_summary",
    "extract_wallet_summary",
    "index_scan_memory",
    "index_wallet_memory",
    "scan_point_id",
    "schedule_scan_memory_index",
    "schedule_wallet_memory_index",
    "should_index_scan",
    "should_index_wallet",
    "wallet_point_id",
]
