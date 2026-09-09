# Wallet Memory Decoupling — Target Architecture & Cutover

> Status: **Planning / partial** (2026-09-09). Live code is NOT yet decoupled;
> this file is the agreed target + the ordered cutover steps.

## Problem

`wallet_memory` (the label graph + entity resolution) must NOT be provided by
rugmunchbot — a standalone Telegram bot. Today the bot both **owns label
provisioning** and **ships frozen copies of the scanner engines** that read the
`wallet_memory` ClickHouse database, entangling it with data it should only
consume:

| File (rugmunchbot) | What it does today | Wrong because |
|---|---|---|
| `src/rugmunchbot/handlers/_scan_handlers/_wallet.py` | imports `_vendor.rag_pkg.scan_memory.schedule_wallet_memory_index` and fires it after every `/wallet` scan → **writes** Qdrant `scanned_wallets` trust/label points itself | Provisioning belongs to the indexer/scanner system; the bot should be read-only |
| `src/rugmunchbot/_vendor/scanner_engines/own_wallet_trust.py`, `own_dex_data/*`, `own_whale_detect.py`, `launchpad_resolve_chain.py` | frozen copies of `rmi-backend/app/domains/scanner/engines/*` that query ClickHouse `wallet_memory.*` directly | duplicated scanner engines; canonical home is `rugmunch-scanner` |
| `src/rugmunchbot/_vendor/rag_pkg/*` | frozen copies of `rmi-backend/app/rag/*` (incl. scan-memory Qdrant writer) | RAG lives in `rugbrain`; vendor copy drifts |

`_vendor/` files are **frozen** — never edit them in place (headers say
"do not edit"; `bash scripts/refresh-vendors.sh` re-vendors). Cutover happens
at **import sites** (handlers) and in the **refresh script**, not inside the
vendored files.

## Canonical owners (target state)

1. **Indexer** (chain-indexer / lite-chain-indexer) writes the label graph:
   ClickHouse `wallet_memory.*` tables — SSOT is
   `wallet_memory.wallet_labels` (see `rmi-backend/app/db/labels_sync.py`
   batched upsert; PG `public.labels` is a downstream consumer).
2. **Scanner** owns label queries + entity resolution + the label-graph API:
   - `rugmunch-scanner/rugmunch_scanner/wallet_memory/` — `storage.py`,
     `clustering.py`, `label_growth.py`, `router.py` (FastAPI router
     `/api/v1/wallet/*`: wallet profiles, deployer intel, entity search —
     consumers: WalletSafe frontend, SENTINEL).
   - `rugmunch-scanner/rugmunch_scanner/scoring/wallet_labeler.py` — behavioral
     labeler (Redis `wallet:labels:{chain}:{address}` etc.).
   - `rugmunch-scanner/rugmunch_scanner/sources/indexer_client.py` — typed,
     fail-open read access to the indexer's ClickHouse outputs.
   - Consolidation roadmap: `rugmunch-scanner/docs/MIGRATION.md` (orders 1-4);
     provenance log in `rugmunch-scanner/PROVENANCE.md`.
3. **RugBrain** (`rugbrain-api:8012`) owns the Qdrant `scanned_tokens` /
   `scanned_wallets` RAG-memory collections and their ingest subscriber
   (`rmi:rag:findings_queue`). Scans flow: scanner publishes → rugbrain
   subscriber embeds+indexes. The bot never writes these directly.

## Target architecture

```
indexer (ClickHouse wallet_memory.* SSOT)
   │  writes labels/transfers/events
   ▼
rugmunch-scanner  ── wallet_memory modules + /api/v1/wallet/* API ──┐
   │  (entity resolution, clustering, label growth)                 │ queries
   ▼                                                                ▼
rugbrain (RAG: scanned_tokens/scanned_wallets Qdrant)        rugmunchbot = THIN CLIENT
   ▲  subscriber indexes scanner findings                       (queries scanner API /
   │                                                            rugbrain API only —
   └── scanner publishes findings ────────────────────────      owns NO labels, writes
                                                                NO wallet memory)
```

Bot rule of thumb: **query, don't own.** If the bot needs a label, trust score,
or wallet-memory hit, it calls the scanner wallet API (`/api/v1/wallet/*`) or
the rugbrain search API (`RAG_API_URL`, `http://rugbrain-api:8012`). It never
computes labels from raw ClickHouse and never writes provisioning data.

## Cutover TODOs

1. [ ] `handlers/_scan_handlers/_wallet.py`: drop the local
   `_vendor.rag_pkg.scan_memory.schedule_wallet_memory_index(result)` fire —
   replace with a read/record call against the scanner wallet-memory API once
   the scanner-side auto-index path (scanner publishes → rugbrain ingests) is
   live for wallet scans. Until then, keep the fire-and-forget (fail-open) so
   reports are never blocked, but log a `[TECH-DEBT]` marker.
2. [ ] `handlers/_scan_handlers/_wallet.py` + `_wallet.py` trust block: replace
   vendored `own_wallet_trust.get_wallet_trust` with a query to
   `rugmunch-scanner` `/api/v1/wallet/*` (or `rmi-backend` public wallet API
   `app/api/v1/public/wallet.py`) once those endpoints expose the 0-100 trust
   + verdict shape the formatter renders.
3. [ ] `scripts/refresh-vendors.sh`: change the vendored-engine source from
   `rmi-backend/app/domains/scanner/engines/*` to `rugmunch-scanner` once the
   rugmunch-scanner ports land (see `rugmunch-scanner/docs/MIGRATION.md`
   orders 2-3). Then delete `_vendor/scanner_engines/own_wallet_trust.py`,
   `own_dex_data/`, `own_whale_detect.py` from the bot and point importers at
   the API.
4. [ ] Remove `_vendor/rag_pkg/scan_memory.py` wallet-indexing path entirely
   after (1): scanned-wallet memory becomes scanner→rugbrain only.
5. [ ] Keep `_vendor/rag_pkg` query-side wrappers (search via `RAG_API_URL`)
   only — re-vendor from `rugbrain`, never from `rmi-backend`.

## Scanner engine home (Task 2 note)

`rugmunch-scanner` is the **single canonical scanner engine** (PRIVATE —
methodology is competitive IP; never mirror to radicle). Duplicated detection
logic still in flight: `rmi-backend/app/domains/scanner{,s}/`, `app/scoring/`,
`app/domains/scanner/engines/*` (live glue), and the bot's frozen
`_vendor/scanner_engines/` copies. Follow `rugmunch-scanner/docs/MIGRATION.md`
for the staged port; rmi-backend keeps **thin import adapters only** (per
`rugmunch-scanner/PROVENANCE.md`), never competing implementations.
