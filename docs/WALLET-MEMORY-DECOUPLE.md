# Wallet Memory Decoupling — Architecture & Cutover

> Status: **Consolidated** (2026-09-10). Label **storage + entity resolution**
> now live on the **indexer** (`GET/POST /api/v1/labels`); the scanner queries
> that API (with a local ClickHouse fallback), keeps risk scoring/detection,
> and rugmunchbot is a thin client. This doc reflects the live code.

## Problem

`wallet_memory` (the label graph + entity resolution) must NOT be provided by
rugmunchbot — a standalone Telegram bot. Historically the bot both **owned label
provisioning** and **shipped frozen copies of the scanner engines** that read the
`wallet_memory` ClickHouse database, entangling it with data it should only
consume. The decoupling below moves label **ownership** to the indexer, keeps
**detection** in the scanner, and reduces the bot to a consumer.

| File (rugmunchbot) | What it did | Resolution |
|---|---|---|
| `handlers/_scan_handlers/_wallet.py` | imported `_vendor.rag_pkg.scan_memory.schedule_wallet_memory_index` and fired it after every `/wallet` scan → **wrote** Qdrant `scanned_wallets` points itself | Provisioning moved off the bot; scanned-wallet memory is scanner→rugbrain. See cutover TODOs. |
| `_vendor/scanner_engines/own_wallet_trust.py`, `own_dex_data/*`, `own_whale_detect.py`, `launchpad_resolve_chain.py` | frozen copies of `rmi-backend/app/domains/scanner/engines/*` that query ClickHouse `wallet_memory.*` directly | Canonical home is `rugmunch-scanner`; `own_wallet_trust` is **deprecated** in favor of the label graph (see `SCANNER-ENGINES-CUTOVER.md`). |
| `_vendor/rag_pkg/*` | frozen copies of `rmi-backend/app/rag/*` (incl. scan-memory Qdrant writer) | RAG lives in `rugbrain`; vendor copy drifts. |

`_vendor/` files are **frozen** — never edit them in place (headers say
"do not edit"; `bash scripts/refresh-vendors.sh` re-vendors). Cutover happens
at **import sites** (handlers) and in the **refresh script**, not inside the
vendored files.

## Canonical owners (live state)

1. **Indexer** (`lite-chain-indexer`) owns label **storage + entity
   resolution** — labels are *data* and live with the on-chain warehouse:
   - `api/routers/labels.py` — the label-graph API:
     - `GET /api/v1/labels/{address}` — entity resolution. Reads
       `wallet_memory.entity_labels` (`FINAL`, ranked by confidence); returns
       `resolved=false` + empty list for unknown addresses (a valid answer,
       not a 404). Optional `chain` filter, bounded `limit` (≤50).
     - `POST /api/v1/labels` — the **canonical write path** for label growth
       and the importer. Idempotent upsert on
       `(address, chain_id, source)`; optional bearer auth
       (`LABEL_API_TOKEN`), write gate `LABEL_API_WRITE_ENABLED`, `503` when
       the store is disabled/unreachable.
   - `api/label_store.py` — dedicated ClickHouse **write** user (reads use the
     SELECT-only `chain_api` user); `entity_labels` is a `ReplacingMergeTree`.
   - SSOT table: `wallet_memory.entity_labels` (9-column canonical schema).
2. **Scanner** (`rugmunch-scanner`) owns **detection + risk scoring** and is a
   **label-API client**:
   - `wallet_memory/indexer_label_client.py` — typed, **fail-open**
     read/write edge to the indexer (`resolve` → GET, `persist` → POST).
     Config: `INDEXER_API_URL` (default `http://127.0.0.1:8100`),
     `INDEXER_API_TOKEN`, `INDEXER_API_TIMEOUT`.
   - `wallet_memory/entity_resolver.py` — entity resolution calls the indexer
     first (`_labels_via_indexer`); only when the indexer is unreachable does
     it fall back to a local ClickHouse read (`storage.get_entity_labels`).
     A reachable-but-empty answer is authoritative (no fallback).
   - `wallet_memory/label_growth.py` + `wallet_memory/label_importer/loaders.py`
     — label writes POST to the indexer (`persist_entity_labels`); direct
     ClickHouse `entity_labels` insert happens **only** when the API is down.
   - Risk scoring / behavioral detection stay in the scanner:
     `wallet_memory/risk_scoring.py`, `scoring/wallet_labeler.py` (Redis
     `wallet:labels:{chain}:{address}`), and the label-growth extractors.
   - Provenance/roadmap: `rugmunch-scanner/PROVENANCE.md`,
     `rugmunch-scanner/docs/MIGRATION.md`.
3. **RugBrain** (`rugbrain-api:8012`) owns the Qdrant `scanned_tokens` /
   `scanned_wallets` RAG-memory collections and their ingest subscriber
   (`rmi:rag:findings_queue`). Scans flow: scanner publishes → rugbrain
   subscriber embeds+indexes. The bot never writes these directly.

## Target architecture

```
indexer (ClickHouse wallet_memory.entity_labels SSOT)
   │  GET/POST /api/v1/labels   (storage + entity resolution)
   ▼
rugmunch-scanner  ── label API client (fail-open) ──────────────────┐
   │  detection + risk scoring + label-growth extractors            │ queries
   │  POSTs label growth to the indexer; GETs entity resolution     │
   ▼                                                                ▼
rugbrain (RAG: scanned_tokens/scanned_wallets Qdrant)        rugmunchbot = THIN CLIENT
   ▲  subscriber indexes scanner findings                       (queries scanner API /
   │                                                            rugbrain API only —
   └── scanner publishes findings ────────────────────────      owns NO labels, writes
                                                                NO wallet memory)
```

Bot rule of thumb: **query, don't own.** If the bot needs a label, trust score,
or wallet-memory hit, it calls the scanner wallet API (`/api/v1/wallet/*`), the
indexer label API (`/api/v1/labels/*`), or the rugbrain search API
(`RAG_API_URL`, `http://rugbrain-api:8012`). It never computes labels from raw
ClickHouse and never writes provisioning data.

## Cutover TODOs

1. [ ] `handlers/_scan_handlers/_wallet.py`: drop the local
   `_vendor.rag_pkg.scan_memory.schedule_wallet_memory_index(result)` fire —
   replace with a read/record call against the scanner wallet-memory API once
   the scanner-side auto-index path (scanner publishes → rugbrain ingests) is
   live for wallet scans. Until then, keep the fire-and-forget (fail-open) so
   reports are never blocked, but log a `[TECH-DEBT]` marker.
2. [ ] `handlers/_scan_handlers/_wallet.py` + `_wallet.py` trust block: replace
   vendored `own_wallet_trust.get_wallet_trust` with a query to the indexer
   label API (`GET /api/v1/labels/{address}`, entity resolution) or the scanner
   `/api/v1/wallet/*` API. `own_wallet_trust` is **deprecated** — the label
   graph supersedes it (decision recorded in `SCANNER-ENGINES-CUTOVER.md`).
3. [ ] `scripts/refresh-vendors.sh`: change the vendored-engine source from
   `rmi-backend/app/domains/scanner/engines/*` to `rugmunch-scanner` once the
   rugmunch-scanner ports land (see `rugmunch-scanner/docs/MIGRATION.md`).
   Then delete `_vendor/scanner_engines/own_wallet_trust.py`,
   `own_dex_data/`, `own_whale_detect.py` from the bot and point importers at
   the API.
4. [ ] Remove `_vendor/rag_pkg/scan_memory.py` wallet-indexing path entirely
   after (1): scanned-wallet memory becomes scanner→rugbrain only.
5. [ ] Keep `_vendor/rag_pkg` query-side wrappers (search via `RAG_API_URL`)
   only — re-vendor from `rugbrain`, never from `rmi-backend`.

## Scanner engine home

`rugmunch-scanner` is the **single canonical scanner engine** (PRIVATE —
methodology is competitive IP; never mirror to radicle). Duplicated detection
logic still in flight: `rmi-backend/app/domains/scanner{,s}/`, `app/scoring/`,
`app/domains/scanner/engines/*` (live glue), and the bot's frozen
`_vendor/scanner_engines/` copies. Follow `rugmunch-scanner/docs/MIGRATION.md`
for the staged port; rmi-backend keeps **thin import adapters only** (per
`rugmunch-scanner/PROVENANCE.md`), never competing implementations.
