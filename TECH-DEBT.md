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
- [x] **Root cause fixed (2026-09-18):** seed batching — `seed_kol_registry`
      now uses ONE connection + ONE commit for the whole seed (was 500+ fsyncs).
      Also added `PRAGMA busy_timeout=5000` on all connections (writer contention).
- [x] **Suite verified (2026-09-18):** 1803 passed / 0 failed / 1 skipped in 7m55s.
- [ ] **Coverage ratchet:** measured **47%** (2026-09-18, );
      CI floor 34; fleet target ≥70%. Feed the ratchet from the 0%-covered set
      first: tools/scanner/_local_v2.py (308 lines), channels/_alert_story.py,
      channels/scheduler/_content_rotation.py, tools/content_lint.py, bot/__main__.py
      — then the <15% cluster (handlers/investigate.py, handlers/groups.py,
      handlers/callback/_inline.py, handlers/chat_ai/_llm/_clients.py, tools/whale_profiler.py).

## Resolved 2026-09-18 — guardrails-ai hub validator (AWS/CF phone-home)

- **What:** the vendored `ai_guardrails.py` initialized `ToxicLanguage` from
  the guardrails **hub** at import time → TCP stall to
  production-kb/telemetry.guardrailsai.com (AWS CloudFront) + a HuggingFace
  model pull (albert-base-v2) on **every fresh process** (bot boot, pytest,
  cron). Measured: `import _clients` **40.5s → 1.9s**; pytest startup
  **38.9s → 0.8s**.
- **Fix:** validator path removed (documented passthroughs); `guardrails-ai*`,
  `spacy*`, `nltk` uninstalled from the bot venv; requirements.txt cleaned.
  ML scanning stays self-hosted: llm-guard sidecar (:8016) + presidio +
  the regex layer.
- **Follow-up (reduction):** `validate_toxic`/`validate_competitor`/
  `full_pipeline` in the vendored file are now passthroughs with no first-party
  callers — delete on the next vendored-file touch.
