# TECH-DEBT.md — rugmunchbot (THE canonical debt ledger)

> Policy: **docs/GOOD-CODE-POLICY.md**. Consolidated 2026-09-18: the old
> `TECH_DEBT.md` (FIXME table) now lives in this file; `TECH_DEBT.md` is a pointer.

> Living ledger. Items are ordered by payoff. Updated 2026-08-21 after the
> platform audit (see rmi-backend `AUDIT-2026-Q3.md` for platform context).

## Done
- [x] Silent `except Exception: pass` in `_vendor/unified_scanner/_finalize_helpers.py`
      → warning-tier logging (mirrors rmi-backend fix, commit series 2026-08-21)
- [x] Production Dockerfile + docker-compose.yml (non-root UID 10002,
      restart policy, named volume) — previously unshippable as a container
- [x] Gitleaks sweep clean; hooks verified

## God files — split plan (first-party >800 lines; `_vendor/` is out of scope)

| File | Lines | Status |
|---|---|---|
| `handlers/chat_ai/_llm/_router.py` | — | ✅ DONE → handlers/chat_ai/_llm/_router/ (package) |
| `config.py` | — | ✅ DONE → config/ (package) |
| `formatters/_scan_report_deepscan.py` | — | ✅ DONE → split (deepscan sections) |
| `handlers/trace.py` | — | ✅ DONE → handlers/trace/ (package) |
| `formatters/_scan_report_common.py` | — | ✅ DONE → split (blocks/layout) |
| `channels/telegram_news/_sources.py` | — | ✅ DONE → one module per source family |
| `channels/daily_market_summary.py` | — | ✅ DONE → assembly vs rendering split |
| `channels/formatters.py` | — | ✅ DONE → per-channel formatter modules |

**Currently >800 (first-party):**

| File | Lines | Status |
|---|---|---|
| `handlers/market.py` | 847 | **OPEN** → split by responsibility |


## Vendored code (`_vendor/`)
- `osint/exploit_db.py` (1094), `osint/exploit_sources.py` (1000),
  `unified_scanner/*` — vendored copies of backend modules.
- **Decision (2026-08-21): KEEP VENDORED** — deploy independence wins while
  the bot ships as a standalone container; the backend's module layout is
  still in flux (platform win 13 domain-packaging). Revisit only after
  `rmi-common` extraction (platform win 15) provides a stable shared lib.

## Known contracts / follow-ups
- ~~`TODO(ghost-admin-key)`~~ **DONE 2026-08-21** — key provisioned from
  the secrets manager (ghost-admin-api-key entry) to
  a runtime secret file (0600), the first path in the vendored
  content_platform resolution chain. Cross-posting enabled.
- Health surface: observability thread serves `/healthz` + `/metrics` on
  :9191 (`RUGMUNCHBOT_METRICS_*`); Docker HEALTHCHECK + compose wired to it.
- Test suite: 16 files, green. Coverage gates not yet wired (platform win 29).

## FIXME ledger (consolidated from TECH_DEBT.md, 2026-09-18)

| # | Location | Item |
|---|---|---|
| 1 | `channels/shill_watch/_evidence.py` | Deleted-post detection — poll-based sweep never sees deletions; needs a Telethon `events.MessageDeleted` handler that re-persists captured evidence with `deleted=True`. |
| 2 | `channels/personal_channel/_ghost.py` | Provision `GHOST_ADMIN_API_KEY` (secrets store: `ghost-admin-api-key`) for the cross-post job. Content API key cannot publish. |

## Testing reliability (2026-09-18)

- [x] **Suite can hang forever — found live.** `test_seed_into_db_round_trip`
      stalled 25+ min in `upsert_kol_identity` D-state on `jbd2_log_wait_commit`.
      Mitigation shipped: `pytest-timeout` + `timeout = 120` in pytest config.
- [ ] **Root cause to fix:** `seed_kol_registry` (db/kol/identity.py) opens a
      connection AND commits **per row** — 500+ rows = 500+ fsyncs. Batch the
      whole seed into ONE transaction (single connection, one commit).
- [ ] **Coverage ratchet:** measured 31.6% (2026-08-23); CI floor 34; fleet
      target ≥70%. Start with the 0%-covered modules from the coverage XML —
      smoke tests first (import + one happy path), then branch coverage.
