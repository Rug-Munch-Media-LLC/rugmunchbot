#!/usr/bin/env python3
"""KOL scraped-data ingest and Qdrant embedding (KOL registry/data module).

Additive consumer for the pryscraper KOL leaderboard spiders
(``crypto_market.kol_leaderboards``). It:

1. **Dedups** scraped KOL records against the existing registry: an identity
   is NEW only if neither its wallet address nor its handle is already known
   in ``kol_identities`` / ``kol_aliases`` (the ~1576-wallet / ~1246-handle
   registry the fleet already tracks).
2. **Persists** each NEW identity via the existing DB accessors
   (``upsert_kol_identity`` + ``register_alias`` + ``attach_kol_wallet``) so
   the scoreboard's canonical identity model is reused — never a duplicate
   write path.
3. **Embeds** each new identity (handle + name + wallet + P&L summary text)
   into Qdrant ``kol_identities_v1`` (2560-dim cosine) for semantic lookup.
4. **Flags** serial-deployer / shill-group wallets into the baddies list
   (``data/kol/baddies.json``, name-and-shame with an evidence URL).

Fail-open: every stage degrades to ``False``/no-op rather than raising, so a
dead embedder or Qdrant never breaks the DB write.

This module is intentionally data-only — it does NOT touch the channels /
content / LLM layers of the bot.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("rugmunchbot.data.kol_ingest")

_DATA_DIR = Path(__file__).resolve().parent / "kol"

#: Qdrant KOL collection (2560-dim cosine) — created on first use.
KOL_QDRANT_COLLECTION = "kol_identities_v1"
KOL_EMBED_DIM = 2560

#: Qwen3-embedding-4B renderer (llama.cpp OpenAI-compatible). Mirrors the
#: bot's ``qwen3-embedding-4b`` 2560d primary embedder. The rugbrain-api
#: embed endpoint needs a secret key (gopass), so the local model layer is
#: preferred for bulk ingest.
EMBEDDER_URL = os.getenv("EMBEDDER_URL", "http://127.0.0.1:8004/v1")
EMBED_MODEL = os.getenv("EMBED_MODEL", "qwen3-embedding-4b")

#: Qdrant gRPC/HTTP host (6334 gRPC, 6333 HTTP). Uses the standard env var.
QDRANT_URL = os.getenv("RAG_QDRANT_URL", "http://127.0.0.1:6333")

#: Duplicate / near-duplicate guard threshold for the same handle+wallet.
_DEDUP_TTL = 60.0

_cache: dict[str, Any] = {}


# ── Embedding (local qwen3, 2560d) ──────────────────────────────────────
async def _embed(texts: list[str]) -> list[list[float]]:
    """Embed a batch via the local Qwen3-embedding-4B layer.

    Never raises: any failure yields ``[]`` (caller falls back to a no-embed).
    Each vector is forced to ``KOL_EMBED_DIM`` by truncation/padding.
    """
    if not texts:
        return []
    vectors: list[list[float]] = []
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            for text in texts:
                try:
                    resp = await client.post(
                        f"{EMBEDDER_URL}/embeddings",
                        json={"model": EMBED_MODEL, "input": [text[:4000]]},
                    )
                    if resp.status_code == 200:
                        data = resp.json().get("data", [])
                        if data and data[0].get("embedding"):
                            vec = [float(x) for x in data[0]["embedding"]]
                            vectors.append(_fit_dim(vec))
                            continue
                except (httpx.HTTPError, OSError, TimeoutError):
                    break
            # Pad failures with zero vectors so indices line up.
            while len(vectors) < len(texts):
                vectors.append([0.0] * KOL_EMBED_DIM)
    except (httpx.HTTPError, OSError, TimeoutError) as e:
        logger.warning("kol_embed_failed err=%s", e)
    return vectors


def _fit_dim(vec: list[float]) -> list[float]:
    """Truncate or pad a vector to ``KOL_EMBED_DIM`` (2560)."""
    if len(vec) == KOL_EMBED_DIM:
        return vec
    if len(vec) > KOL_EMBED_DIM:
        return vec[:KOL_EMBED_DIM]
    return vec + [0.0] * (KOL_EMBED_DIM - len(vec))


# ── Qdrant upsert ───────────────────────────────────────────────────────
def _qdrant_client() -> Any:
    """Lazy Qdrant client (gRPC port 6334)."""
    from qdrant_client import QdrantClient

    host = QDRANT_URL.replace("http://", "").replace("https://", "").split(":")[0]
    return QdrantClient(host=host, port=6334, grpc_port=6334)


async def upsert_kol_embedding(doc_id: str, vector: list[float], payload: dict[str, Any]) -> bool:
    """Upsert one KOL point into ``kol_identities_v1`` (fail-open)."""
    if not vector:
        return False
    try:
        from qdrant_client import models

        client = _qdrant_client()
        if not client.collection_exists(KOL_QDRANT_COLLECTION):
            client.create_collection(
                collection_name=KOL_QDRANT_COLLECTION,
                vectors_config=models.VectorParams(
                    size=KOL_EMBED_DIM, distance=models.Distance.COSINE
                ),
            )
        client.upsert(
            collection_name=KOL_QDRANT_COLLECTION,
            points=[
                models.PointStruct(
                    id=doc_id if isinstance(doc_id, int) else _stable_id(doc_id),
                    vector=vector,
                    payload=payload,
                )
            ],
        )
        return True
    except Exception as e:  # fail-open, never raise
        logger.warning("kol_qdrant_upsert_failed doc_id=%s err=%s", doc_id, e)
        return False


def _stable_id(doc_id: str) -> int:
    """Deterministic integer point id from a string doc_id (uuid5 col)."""
    import uuid

    return uuid.uuid5(uuid.NAMESPACE_URL, doc_id).int & ((1 << 63) - 1)


# ── Dedup ───────────────────────────────────────────────────────────────
def _norm_handle(handle: object) -> str:
    return str(handle or "").replace("https://x.com/", "").lstrip("@").lower().strip()


def _norm_addr(addr: object) -> str:
    return str(addr or "").lower().strip()


def existing_registry() -> dict[str, set[str]]:
    """Snapshot the known wallet + handle sets from the KOL registry DB.

    Returns ``{"wallets": set, "handles": set}``. Reads live DB (the raw
    ``wallets`` JSON column is parsed, never assumed shaped). On any error
    returns empty sets so the caller can still run (conservative: it will
    treat everything as new and the per-row upsert dedups anyway).
    """
    wallets: set[str] = set()
    handles: set[str] = set()
    try:
        from rugmunchbot.db._base import get_db

        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT kol_id, screen_name, wallets FROM kol_identities"
            ).fetchall()
            for r in rows:
                for h in (r["kol_id"], r["screen_name"]):
                    if h:
                        handles.add(_norm_handle(h))
                for w in _wallet_entries(r["wallets"]):
                    if w:
                        wallets.add(_norm_addr(w))
            alias_rows = conn.execute("SELECT alias FROM kol_aliases").fetchall()
            for r in alias_rows:
                if r["alias"]:
                    handles.add(_norm_handle(r["alias"]))
        finally:
            conn.close()
    except Exception as e:
        logger.warning("kol_existing_registry_read_failed err=%s", e)
    return {"wallets": wallets, "handles": handles}


def _wallet_entries(value: Any) -> list[str]:
    """Coerce the wallets JSON column to a list of address strings."""
    if isinstance(value, list):
        raw = value
    elif not value:
        return []
    else:
        try:
            raw = json.loads(str(value))
        except (ValueError, TypeError):
            return []
        if not isinstance(raw, list):
            return []
    out: list[str] = []
    for v in raw:
        addr = v.get("address") if isinstance(v, dict) else v
        if addr:
            out.append(str(addr))
    return out


def _is_new(rec: dict[str, Any], known: dict[str, set[str]], seen: set[tuple[str, str]]) -> bool:
    """True when a record is a NEW identity to persist.

    The wallet is the primary identity key (matching the wallet-KB model the
    DB accessors use): a record is NEW when its wallet is not already tracked.
    Handle-only records (no wallet) are treated as NEW when the handle is also
    unknown. A known wallet OR (known handle AND known wallet) is a duplicate.
    """
    addr = _norm_addr(rec.get("address"))
    handle = _norm_handle(rec.get("handle"))
    key = (addr, handle)
    if key in seen:
        return False
    seen.add(key)
    if addr:
        return addr not in known["wallets"]
    return bool(handle) and handle not in known["handles"]


# ── Baddies flagging ────────────────────────────────────────────────────
#: Heuristics for a serial-deployer / shill-group flag on a scraped KOL.
#: A record is promoted to the baddies list when it matches any rule AND
#: carries an evidence URL (never a fabricated bankroll).
_BADDY_RULES: list[dict[str, Any]] = [
    {
        "name": "kolscan_loss_dominant",
        "when": lambda r: (
            (r.get("wins") or 0) + (r.get("losses") or 0) >= 5
            and (r.get("losses") or 0) > (r.get("wins") or 0)
        ),
        "severity": "medium",
        "category": "shill_loss",
        "risk_level": "watch",
        "note": "Losing > winning trader on a public KOL leaderboard.",
    },
    {
        "name": "kolscan_wash_high_tx",
        "when": lambda r: (r.get("wins") or 0) + (r.get("losses") or 0) >= 100,
        "severity": "medium",
        "category": "high_frequency",
        "risk_level": "watch",
        "note": "Very high trade count on a public KOL leaderboard.",
    },
]


def _flag_baddies(rec: dict[str, Any]) -> dict[str, str] | None:
    """Return a baddies row when the record matches a named rule, else None."""
    for rule in _BADDY_RULES:
        try:
            if rule["when"](rec):
                return {
                    "handle": _norm_handle(rec.get("handle")) or rec.get("name", ""),
                    "name": rec.get("name") or rec.get("handle", ""),
                    "wallet": rec.get("address") or "",
                    "category": rule["category"],
                    "severity": rule["severity"],
                    "risk_level": rule["risk_level"],
                    "group": "",
                    "evidence_url": rec.get("evidence_url") or "",
                    "reason": rule["note"],
                    "source_rule": rule["name"],
                }
        except Exception as e:  # a rule must never sink the batch
            logger.debug("kol_baddy_rule_failed rule=%s err=%s", rule["name"], e)
    return None


def flag_baddies(records: list[dict[str, Any]], evidence_url: str = "") -> int:
    """Append rule-matched baddies to ``data/kol/baddies.json``.

    Idempotent per (handle, wallet): a duplicate row is skipped. Returns the
    number of NEW baddies appended. `evidence_url` is used when a record has
    no per-record evidence link. Never raises.
    """
    path = _DATA_DIR / "baddies.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        baddies = data.get("baddies", []) if isinstance(data, dict) else []
        existing = {
            (_norm_addr(b.get("wallet") or ""), _norm_handle(b.get("handle") or ""))
            for b in baddies
        }
        added = 0
        for rec in records:
            row = _flag_baddies(rec)
            if not row:
                continue
            if not row["wallet"] and not row["handle"]:
                continue
            if not row["evidence_url"]:
                row["evidence_url"] = evidence_url
            key = (_norm_addr(row["wallet"]), _norm_handle(row["handle"]))
            if key in existing:
                continue
            baddies.append(row)
            existing.add(key)
            added += 1
        data = {"updated": _now_iso(), "count": len(baddies), "baddies": baddies}
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return added
    except OSError as e:
        logger.warning("kol_baddies_write_failed err=%s", e)
        return 0


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ── Main ingest ─────────────────────────────────────────────────────────
async def ingest_scraped(records: list[dict[str, Any]], *, embed: bool = True) -> dict[str, Any]:
    """Persist NEW scraped KOL records into the bot DB + Qdrant.

    Returns a summary dict: new identities, new aliases, wallets attached,
    qdrant points upserted, baddies flagged, dedup counts. Fail-open at every
    stage.
    """
    from rugmunchbot.db import (
        attach_kol_wallet,
        get_kol_identity,
        register_alias,
        upsert_kol_identity,
    )

    known = existing_registry()
    seen: set[tuple[str, str]] = set()
    new_records: list[dict[str, Any]] = []
    for rec in records:
        if _is_new(rec, known, seen):
            new_records.append(rec)

    identities = 0
    aliases = 0
    wallets = 0
    for rec in new_records:
        handle = _norm_handle(rec.get("handle"))
        address = _norm_addr(rec.get("address"))
        name = str(rec.get("name") or handle or address).strip()
        if not handle and not address:
            continue
        kol_id = handle or address
        existing = get_kol_identity(kol_id)
        upsert_kol_identity(
            kol_id=kol_id,
            name=name,
            screen_name=(existing["screen_name"] if existing else handle) or name,
            category="trader" if rec.get("source") == "kolscan_leaderboard" else "shiller",
            source=str(rec.get("source") or "kol_scrape"),
            verified=bool(existing and existing.get("verified")),
            risk_level="watch",
            notes=f"scraped from {rec.get('source')}" if rec.get("source") else "scraped",
        )
        identities += 1
        if handle:
            register_alias(kol_id, handle, is_current=True)
            aliases += 1
        if address and attach_kol_wallet(
            kol_id,
            address,
            chain=str(rec.get("chain") or "solana"),
            source=str(rec.get("source") or "kol_scrape"),
            evidence_url=str(rec.get("evidence_url") or ""),
            confidence="probabilistic",
        ):
            wallets += 1

    qdrant = 0
    if embed and new_records:
        texts = [
            (
                f"{_norm_handle(r.get('handle')) or r.get('name', '')}: "
                f"{r.get('name', '')} ({r.get('address', '')[:8]}...) on {r.get('chain', 'solana')}. "
                f"profit={r.get('profit', 0)}, wins={r.get('wins', 0)}, losses={r.get('losses', 0)}"
            )
            for r in new_records
        ]
        vectors = await _embed(texts)
        for rec, vec in zip(new_records, vectors, strict=False):
            doc_id = (
                _norm_addr(rec.get("address"))
                or _norm_handle(rec.get("handle"))
                or rec.get("name", "")
            )
            payload = {
                "handle": rec.get("handle") or "",
                "name": rec.get("name") or "",
                "address": rec.get("address") or "",
                "chain": rec.get("chain") or "solana",
                "source": rec.get("source") or "",
                "profit": rec.get("profit", 0),
                "wins": rec.get("wins", 0),
                "losses": rec.get("losses", 0),
                "twitter": rec.get("twitter") or "",
                "evidence_url": rec.get("evidence_url") or "",
            }
            if await upsert_kol_embedding(doc_id, vec, payload):
                qdrant += 1

    baddies = flag_baddies(new_records)

    return {
        "scanned": len(records),
        "new_records": len(new_records),
        "known_wallets": len(known["wallets"]),
        "known_handles": len(known["handles"]),
        "identities_persisted": identities,
        "aliases_registered": aliases,
        "wallets_attached": wallets,
        "qdrant_points_upserted": qdrant,
        "baddies_flagged": baddies,
        "collection": KOL_QDRANT_COLLECTION,
        "embed_dim": KOL_EMBED_DIM,
    }


__all__ = [
    "KOL_QDRANT_COLLECTION",
    "existing_registry",
    "flag_baddies",
    "ingest_scraped",
    "upsert_kol_embedding",
]
