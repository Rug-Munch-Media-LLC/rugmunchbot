# RUGMUNCHBOT EXTRACTION CHECKLIST

> **Canonical (2026-08-14):** the LIVE bot is `rmi-backend/app/domains/telegram/rugmunchbot/`
> (99 `.py` files — this supersedes the older "64 modules" figure in
> `docs/RUGMUNCHBOT-EXTRACTION.md`, which predates the `tools/` layer).
> The standalone repo (`/srv/work/repos/rugmunchbot`) is the designated OSS home.
> This checklist is the **executable** companion to `docs/RUGMUNCHBOT-EXTRACTION.md`
> (design rationale) and `docs/RUGBRAIN_RUGMUNCHBOT_OSS_PLAN.md` (RugBrain boundary).
> Everything here is PLANNING + SCAFFOLD — no code was moved, no commit was made.

---

## 1. Module Map — OSS product vs Private config/glue

### 1.1 The live tree (99 modules, inside rmi-backend today)

```
rugmunchbot/                                  # 99 .py
├── bot.py (507)            entry: Application build, _reg(), gates, main()
├── config.py (696)         ⚠️ PRIVATE CONSTANTS — see §3
├── db.py (SQLite users/tiers/quota/refcodes)
├── scanner.py (scan engines — gmgn_enrich + scan_enrich + scan_memory coupling)
├── admin_state.py / ai_chat.py / alert_worker.py / anti_abuse.py /
│   broadcast_worker.py / daily_context.py / data_resolver.py / flood_control.py /
│   keyboards.py / prompts.py / states.py / ta_indicators.py
├── commands/    (10)  chart events portfolio premium premium_extra premium_v2
│                      rugmap simulate smartmoney __init__
├── handlers/    (28)  account alerts api_access ask chat defi error groups holders
│   │                  inline investigate market message owner payments prediction
│   │                  price report research scanning sentiment trace watchlist webhook
│   │                  _help_text _market_helpers _scanning_helpers __init__
│   ├── callback/ (4)  __init__ _admin _inline _run_cards
│   └── chat_ai/  (6)  __init__ _format _helpers _llm _parts _trace
├── formatters/  (5)  __init__ _base _sentinel _sentinel_helpers _wallet
├── services/    (2)  __init__ visuals
├── support/     (2)  __init__ logging          ← self-contained structlog shim
└── tools/       (21) __init__ _cache _client birdeye budget guardrails intent market
                      menus onchain prediction_markets price rag_lookup registry scanner
                      security ta token_meta trend_algo trending web
```

### 1.2 Classification (what each module becomes in the OSS repo)

| Class | Modules | OSS treatment |
|---|---|---|
| **PURE OSS PRODUCT** (no `app.*` coupling) | `bot.py`*, `config.py` (rewritten), `db.py`*, `states.py`, `flood_control.py`*, `anti_abuse.py`, `admin_state.py`*, `broadcast_worker.py`, `keyboards.py`, `prompts.py`, `ta_indicators.py`, `services/visuals.py`, `support/logging.py`, most of `formatters/`, most of `tools/`, all `commands/` (modulo lazy imports), all plain `handlers/*` | Ship as-is after import rewrite + env config. `*` = has `app.core`/`app.domains.telegram.*` lazy or redis coupling → needs shim/adapter (see §5). |
| **PRIVATE CONFIG** (never ships) | `config.py` §3 blocks: gopass token paths, `_RMI_AUTH_SECRET_FILES`, `OWNER_IDS` default, `CHANNELS` numeric IDs, `TIERS`/`TOP_UP_PACKS`/`BUY_STARS_PACKS`/`SCAN_PACKS` pricing, `JUPITER_FEE_ACCOUNT`/`JUPITER_ULTRA_ACCOUNT` wallets, `DEX_REF_LINKS` ref codes, branding URLs | Stripped to env/config — full mapping in §3. |
| **PRIVATE GLUE — stays in rmi-backend** (or becomes provider protocol) | `app.domains.telegram.headless` (4 imports in `handlers/groups.py`), `voice_chat` (3 in groups.py), `scanner_bridge.ScannerBridge` (`handlers/price.py:29` top-level), `access_guard` (3 files), `app.core.redis` (admin_state, alert_worker, daily_context — wait: flood_control reads REDIS_* env), `app.rag.scan_memory` (scanner.py top-level), `app.domains.scanner.gmgn_enrich` (scanner.py top-level), `app.domains.scanner.scan_enrich` (scanner.py lazy), `app.domains.scanner.engines.launchpad_registry` (formatters/_sentinel lazy), `app.domains.scanner.engines.event_correlation` (commands/events lazy), `app.domains.scanner.unified.scoring.BLUECHIP_ADDRESSES` (formatters/_sentinel lazy) | Protocol seam + optional adapter; OSS default = public API (DexScreener/GoPlus/Honeypot) or no-op fallback. |
| **PRIVATE DATA** (never ships) | `data/bot_users.db` (live SQLite seed — users, tiers, refcodes) | `.gitignore`d; OSS creates empty DB on first run via `db.py`. |
| **TESTS** | 49 files in rmi-backend `tests/` referencing rugmunchbot (~12.5k lines) — these are the LIVE tests | Port (Stage 4) — replace the standalone repo's 9 stale test files that exercise dead code. |

---

## 2. Exact Modules: MOVE vs STAY

### 2.1 MOVES to `rugmunchbot/src/rugmunchbot/` (74 modules)

Copied verbatim (no behavior change), then import-rewritten:

```
bot.py  config.py  db.py  scanner.py  admin_state.py  ai_chat.py  alert_worker.py
anti_abuse.py  broadcast_worker.py  daily_context.py  data_resolver.py  flood_control.py
keyboards.py  prompts.py  states.py  ta_indicators.py
support/{__init__,logging}.py  services/{__init__,visuals}.py
formatters/{__init__,_base,_sentinel,_sentinel_helpers,_wallet}.py
commands/{__init__,chart,events,portfolio,premium,premium_extra,premium_v2,rugmap,simulate,smartmoney}.py
handlers/{__init__,_help_text,_market_helpers,_scanning_helpers,account,alerts,api_access,ask,chat,
  defi,error,groups,holders,inline,investigate,market,message,owner,payments,prediction,price,
  report,research,scanning,sentiment,trace,watchlist,webhook}.py
handlers/callback/{__init__,_admin,_inline,_run_cards}.py
handlers/chat_ai/{__init__,_format,_helpers,_llm,_parts,_trace}.py
tools/{__init__,_cache,_client,birdeye,budget,guardrails,intent,market,menus,onchain,
  prediction_markets,price,rag_lookup,registry,scanner,security,ta,token_meta,trend_algo,trending,web}.py
```

### 2.2 STAYS in rmi-backend (private platform/glue — 0 copies)

| Module | Why it stays | OSS replacement |
|---|---|---|
| `app/core/logging.py` | shared platform logger | `support/logging.py` (already self-contained structlog shim — 100% portable) |
| `app/core/redis.py` (`get_redis_async`) | shared infra client | `support/redis.py` shim (new, ~20 lines) or optional lazy import in the 3 files |
| `app/rag/scan_memory.py` | RAG product memory | `tools/rag_lookup.py` HTTP path or no-op fallback in scanner.py |
| `app/domains/scanner/gmgn_enrich.py` / `scan_enrich.py` | scanner product enrichment | `tools/scanner.py` public-API path; protocol seam |
| `app/domains/scanner/engines/*` | scanner product engines | lazy-import → optional adapter, fail-open (matches existing lazy pattern) |
| `app/domains/telegram/headless.py` (+ _client/_credentials/_models) | private Telegram client infra | `integrations/headless.py` stub (no-op) — groups.py already lazy-imports |
| `app/domains/telegram/voice_chat.py` | private voice-chat API | `integrations/voice_chat.py` stub (no-op) |
| `app/domains/telegram/scanner_bridge.py` | bot↔scanner HTTP bridge to private backend | `data_resolver.py` + `tools/registry.py` `backend_json_*` already call `BACKEND_URL` — becomes `RMI_BACKEND_URL` env |
| `app/domains/telegram/access_guard.py` | owner-alert rate limiter | `handlers/owner.py` `is_owner` gating; port the 10-min TTL alert into `anti_abuse.py` or drop |
| `app/domains/telegram/secure_launcher.py` + `log_filter.py` | gopass token plumbing | NEW `src/rugmunchbot/secure_launcher.py` env-only (no gopass) |
| `app/domains/telegram/adminbot/`, `rmiadminbot.service`, `router.py` | admin bot + routing | not part of OSS v1 |

### 2.3 Import-rewrite pattern (mechanical)

| From (live) | To (OSS) |
|---|---|
| `from app.domains.telegram.rugmunchbot.X import Y` | `from rugmunchbot.X import Y` |
| `from app.domains.telegram.rugmunchbot.formatters import Y` | `from rugmunchbot.formatters import Y` |
| `from app.domains.telegram.rugmunchbot.tools.registry import Y` | `from rugmunchbot.tools.registry import Y` |
| `from app.core.logging import get_logger` | `from rugmunchbot.support.logging import get_logger` (already the live pattern) |
| `from app.domains.telegram.rugmunchbot.config import OWNER_IDS, TIERS, ...` | `from rugmunchbot.config import ...` (names preserved) |

The live tree **already** uses `support/logging.py` everywhere (46 files), so the `app.core.logging` rewrite is already done in-tree. The only remaining `app.*` imports to handle are the glue list in §2.2.

---

## 3. config.py Private Bits → Env/Config (the critical rewrite)

`config.py` (696 lines) is imported by ~56 modules and re-exported via `formatters/__init__.py` +
package `__init__.py`. Rewrite FIRST, in ONE atomic commit (rename-failure lesson, §3.0 of the
RUGBRAIN plan). Do not rename-in-place; rewrite the file then fix the ~56 importers.

| Current (private) | Line(s) | OSS replacement |
|---|---|---|
| `_get_token("rmi/rugmunchbot/bot_token", [...])` gopass call | 51–73 | `BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")` — no gopass, no subprocess |
| `OWNER_IDS = {...os.getenv("TELEGRAM_ALLOWED_USERS", "7075336557")...}` | 74 | `BOT_OWNER_IDS` env (comma ints), **empty default**; owner commands disabled when unset |
| `WEBSITE_URL="https://rugmunch.io"`, `WEB_APP_URL`, `TWITTER_URL`, `TELEGRAM_CHANNEL`, `TELEGRAM_GROUP`, `SUPPORT_EMAIL` | 80–85 | `BOT_WEBSITE_URL` / `BOT_TWITTER_URL` / `BOT_TELEGRAM_CHANNEL` / `BOT_TELEGRAM_GROUP` / `BOT_SUPPORT_EMAIL`, empty defaults |
| `BOT_DESCRIPTION` / `BOT_SHORT_DESCRIPTION` (branded blurb) | 88–102 | `BOT_DESCRIPTION` / `BOT_SHORT_DESCRIPTION` env, neutral default |
| `BACKEND_HOST = "rmi-backend:8000" if _IN_DOCKER else "localhost:8000"` | 110–112 | `RMI_BACKEND_URL` env, default `http://localhost:8000`; drop docker-compose service name |
| `_RMI_AUTH_SECRET_FILES = ("/srv/secrets/rmi_auth_token", "/run/secrets/rmi_auth_token")` | 123–126 | removed entirely |
| `_get_api_key()` → `_get_token("rmi/backend/rmi_auth_token", ["RMI_AUTH_TOKEN"])` | 129–139 | `RMI_API_KEY = os.getenv("RMI_API_KEY", "")` — no secret files, no gopass |
| `SENTINEL_URL = f"{BACKEND_URL}/api/v1/x402-tools/sentinel_scan"` | 187 | `SENTINEL_URL` env (operator's own deployment); **x402 never assumed** — `ScannerProvider` protocol instead |
| `TIERS` (free/scout/hunter/pro with Stars + USD pricing) | 199–284 | **Billing stays private.** OSS ships single `free` tier with env quotas: `BOT_FREE_SCANS_PER_WEEK` (25), `BOT_FREE_AI_MSGS_PER_WEEK` (15) |
| `TOP_UP_PACKS` / `BUY_STARS_PACKS` / `SCAN_PACKS` (Stars amounts) | 291–410 | removed; `BillingProvider` protocol stub (`plugins/billing.py`) |
| `DEX_REF_LINKS` (`ref=rugmunch`, `handle=rugmunch`, `start=x5pyi0`, `?ref=crmuncher`, `?ref=crm`) | 432–526 | `BOT_DEX_REF_LINKS` env JSON or `RMI_REF_GMGN`/`RMI_REF_DEXTOOLS` per-platform env; ship templates with `ref=` placeholder |
| `JUPITER_FEE_ACCOUNT`, `JUPITER_ULTRA_ACCOUNT` (fee wallets) | 544–545 | **removed from OSS** — revenue wallets stay private; gitleaks rule for wallet patterns |
| `CHANNELS = {"main": "-1002056885429", ...}` (5 numeric IDs) | 562–568 | `BOT_CHANNELS` env JSON (`{"alerts": "-100..."}`), no defaults; broadcast/alerts disabled when unset |
| `REF_CODES = {"gmgn": "rugmunch", ...}` (formatters/_base.py:89) | _base 89–96 | `RMI_REF_GMGN`/`RMI_REF_DEXTOOLS` env, empty default (env override already coded via `_ref_code()`) |
| `LLAMA_HOST = os.getenv("LLAMA_HOST", "172.22.0.1")` (chat_ai/_llm.py:39) | _llm 39–45 | default `127.0.0.1`; `BOT_LLM_API_BASE`/`BOT_LLM_API_KEY` |
| `BRIDGE_CONTEXT_ENDPOINT` default `http://127.0.0.1:8095/chat/context` (web.py, chat_ai/__init__.py) | web 26, chat_ai 172 | `BOT_BRIDGE_CONTEXT_ENDPOINT` env; no-op when unset |
| `SEARXNG_URL` default `http://127.0.0.1:8889` | web 27 | `SEARXNG_URL` env (this one is fine as-is — self-hosted, no secret) |
| `BIRDEYE_API_KEY` via `_get_token("rmi/infra/birdeye_api_key", [...])` (tools/birdeye.py:77) | birdeye 77 | `BIRDEYE_API_KEY` env only — no gopass |
| `RUGMUNCH_SCAM_DENYLIST` env (anti_abuse.py) | — | env passthrough, fine |
| `BOT_DB_PATH` (db.py:32), `REDIS_*` (flood_control.py:64) | — | env passthrough, fine |
| `BETA_MODE = os.environ.get("RMI_BOT_BETA_MODE", "true")` | 197 | `RMI_BOT_BETA_MODE` env (OSS default `false` so quotas actually enforce) |

**Never in OSS:** secrets-manager paths, secret-file paths (`/srv/secrets/*`, `/run/secrets/*`),
numeric owner/channel IDs, fee wallets, Stars pricing, X-API-Key values, `172.22.x.x` bridge IPs.

---

## 4. Ordered Migration Steps (atomic units, green at every commit)

> Shared rule (RUGBRAIN plan §3.0): ONE extraction unit per commit; monorepo stays green; run
> the rename checklist (grep → lazy maps → atomic change → full suite → gitleaks) per module.

### Stage 0 — Truth sync (docs only, no code) ✅ DONE for docs
- [x] Fix README/ARCHIVED contradiction (2026-08-14) — README now says "future OSS home", stale tree marked DEPRECATED
- [x] `bot/app/telegram_bot/DEPRECATED.md` written
- [ ] **NEW (this checklist):** delete or `_ARCHIVED/` the stale `bot/app/telegram_bot/` tree (its 86 tests are dead) — REPLACED by scaffold below
- [ ] Decide fate of stale `tests/` (9 files testing dead code) → delete, replaced by ported live tests (Stage 4)

### Stage 1 — Scaffold OSS structure (this PR, no code copied)
- [x] `pyproject.toml` → `src/` layout, deps from live bot (see §7)
- [x] `src/rugmunchbot/` stubs: `bot.py`, `config.py`, `db.py`, `scanner.py`, `states.py`, `flood_control.py`, `anti_abuse.py`, `admin_state.py`, `support/logging.py`, `formatters/`, `commands/`, `handlers/`, `tools/`, `services/visuals.py` — each a docstring stub pointing at the live file
- [x] `src/rugmunchbot/plugins/` — `billing.py` (BillingProvider), `scanner.py` (ScanProvider), `data.py` (DataProvider), `llm.py` (LLMRouter) protocol stubs
- [x] `README.md` (self-hosting, no internals), `MIGRATION.md`, `.env.example` (env-only), `Makefile`/CI update
- [ ] Run `ruff check src/`, `gitleaks detect --no-git` on the scaffold — zero findings

### Stage 2 — Port PURE surface first (zero backend deps)
1. `support/logging.py` → verbatim (already standalone)
2. `formatters/*`, `keyboards.py`, `prompts.py`, `states.py`, `ta_indicators.py`, `services/visuals.py` → verbatim
3. `flood_control.py`, `anti_abuse.py`, `broadcast_worker.py` → verbatim (redis optional via `support/redis.py` shim)
4. `db.py`, `admin_state.py` → verbatim + billing stripped to `BillingProvider`
5. Run ported live tests for these modules — green

### Stage 3 — Port tools + data layer (public APIs)
6. `tools/*` (21) → verbatim; `tools/birdeye.py` key resolution → env-only
7. `data_resolver.py` → provider-injected; `tools/registry.py` `backend_json_*` reads `RMI_BACKEND_URL`
8. `daily_context.py`, `ai_chat.py` → backend chat API via env + local LLM fallback
9. `scanner.py` → `ScanProvider` protocol; gmgn_enrich/scan_enrich/scan_memory → optional adapters (fail-open)

### Stage 4 — Port commands + handlers (the 58KB god files)
10. `commands/*` (10) → verbatim + lazy-import adapters for events/rugmap
11. `handlers/*` (28) → split `scanning.py` into `scan_flow` (portable) + `scan_backend` (seam); `groups.py` headless/voice_chat → no-op adapters; `owner.py` gated by `BOT_OWNER_IDS`
12. `handlers/callback/*`, `handlers/chat_ai/*` → verbatim + LLMRouter seam
13. `bot.py` → verbatim; replace `secure_launcher` gopass with env-only `src/rugmunchbot/secure_launcher.py`

### Stage 5 — Test parity + hardening
- [ ] Port the 49 live test files (~12.5k lines) → replace stale 9
- [ ] Coverage gate ≥60% on live code; respx-mock external APIs
- [ ] `mypy --strict`, `ruff`, `gitleaks`, `bandit` gates in CI
- [ ] `RUGMUNCHBOT-EXTRACTION.md` §5 TODO items (SQLite WAL, JobQueue persistence, rate-limit race) — only AFTER green

### Stage 6 — Cutover (private-side, one release cycle)
- [ ] systemd unit → this repo's venv (`python -m rugmunchbot` or `secure_launcher`)
- [ ] rmi-backend keeps thin re-export shim `app.domains.telegram.rugmunchbot → rugmunchbot` for one release, then remove
- [ ] Repo hygiene: tag v0.1.0, add to `push-all-repos.sh`, fleet-compliance-check

---

## 5. Coupling Inventory — app.* imports still in the live tree (to sever)

| File | Import | Type | Sever (Stage) |
|---|---|---|---|
| `scanner.py` | `app.domains.scanner.gmgn_enrich.enrich_scan_with_gmgn` | **top-level** | ScanProvider adapter (S3) — HARD BLOCKER if not done |
| `scanner.py` | `app.domains.scanner.scan_enrich.enrich_scan` | lazy | ScanProvider adapter (S3) |
| `scanner.py` | `app.rag.scan_memory.schedule_scan_memory_index, should_index_scan` | **top-level** | no-op/HTTP adapter (S3) — HARD BLOCKER |
| `handlers/price.py` | `app.domains.telegram.scanner_bridge.ScannerBridge` | **top-level** | backend_json_get (S3) — HARD BLOCKER |
| `handlers/groups.py` | `app.domains.telegram.headless.{get_headless_client,_client_instance}` | lazy (4×) | no-op adapter (S4) |
| `handlers/groups.py` | `app.domains.telegram.voice_chat.{get_voice_chat_status,create_voice_chat,end_voice_chat}` | lazy (3×) | no-op adapter (S4) |
| `handlers/callback/_admin.py`, `handlers/owner.py`, `handlers/groups.py` | `app.domains.telegram.access_guard` | top-level | port 10-min alert to anti_abuse or drop (S4) |
| `admin_state.py`, `alert_worker.py`, `daily_context.py` | `app.core.redis.get_redis_async` | top-level | `support/redis.py` shim (S2) |
| `formatters/_sentinel.py` | `app.domains.scanner.engines.launchpad_registry.detect_launchpad`; `app.domains.scanner.unified.scoring.BLUECHIP_ADDRESSES` | lazy | optional adapter (S2) |
| `commands/events.py` | `app.domains.scanner.engines.event_correlation.correlate_news_with_price` | lazy | optional adapter (S3) |
| `bot.py`, `scanner.py`, `handlers/chat_ai/*`, `handlers/callback/_inline.py` | headless/voice_chat/scan_memory (lazy) | lazy | no-op adapters |

**NOT coupled (verified):** `app.db` (bot has own `db.py`), `app.config`/`app.core.config` (bot's
`config.py` self-contained), `app.domains.telegram.router` (workers standalone via systemd).

---

## 6. Scrub List — MUST be removed before public push (see also §3)

| # | Item | Location(s) | Action |
|---|---|---|---|
| 1 | secrets-manager path (bot token) | config.py:71, secure_launcher.py:36 | env only |
| 2 | secrets-manager path (backend auth token) | config.py:139 | env only |
| 3 | secrets-manager path (birdeye key) | tools/birdeye.py:77 | env only |
| 4 | secret files `/srv/secrets/rmi_auth_token`, `/run/secrets/rmi_auth_token` | config.py:124–125 | delete |
| 5 | owner ID `7075336557` (env default) | config.py:74 | empty default |
| 6 | owner ID `7075336557` (hardcoded guard) | handlers/owner.py:375 | env-driven |
| 7 | owner ID example `7075336557` | handlers/callback/_admin.py:75 | `<owner-id>` placeholder |
| 8 | channel IDs `-1002056885429`, `-1003982391578`, `-1003818352164`, `-1003762675055`, `-1003937506770` | config.py:563–567 | `BOT_CHANNELS` env |
| 9 | fee wallets `8ouwE3sx6im8pDVwKRZ6n2QNjZwLmrBQ1XTiHhsXge39`, `Dkrbs5cWeaVspAbCHUM4ds6kJdZeuJkgmaje9w9Urtfh` | config.py:544–545 | remove + gitleaks wallet rule |
| 10 | DEX ref codes `ref=rugmunch`, `handle=rugmunch`, `start=x5pyi0`, `?ref=crmuncher`, `?ref=crm` | config.py:436–522, formatters/_base.py:89–96 | env placeholders |
| 11 | backend auth `RMI_AUTH_TOKEN` semantics + `X-API-Key` bypass docs | config.py:117–122 | `RMI_API_KEY` env, neutral docs |
| 12 | internal bridge IP `172.22.0.1` | handlers/chat_ai/_llm.py:39 | `127.0.0.1` default |
| 13 | x402 endpoint `/api/v1/x402-tools/sentinel_scan` (private payment gate) | config.py:187, formatters/_sentinel.py:144/150, _sentinel_helpers.py:62 | `ScannerProvider` protocol |
| 14 | private backend routes `/api/v1/databus/*`, `/api/v1/rag/v2/search`, `/api/v1/chat`, `/api/v1/scanner/*`, `/api/v1/market/*` | ai_chat.py, data_resolver.py, tools/*, daily_context.py, onchain.py, rag_lookup.py | `RMI_BACKEND_URL` + documented as operator-supplied endpoints |
| 15 | branding `rugmunch.io`, `x.com/RugMunch`, `t.me/RugMunchAlerts`, `t.me/RugMunchChat`, `biz@rugmunch.io` | config.py:80–102 + ~50 files mention "RugMunch" in strings | `BOT_*` env; neutral branding (see §8 note) |
| 16 | `data/bot_users.db` (live user DB) | rugmunchbot/data/ | never ship; `.gitignore` `data/*.db` |
| 17 | Stars pricing/`premium` tier values (USD + XTR) | config.py TIERS/TOP_UP/BUY_STARS/SCAN_PACKS | private; `BillingProvider` |
| 18 | systemd unit + `secure_launcher` gopass plumbing (deploy methodology) | fleet-infra (systemd), rmi-backend secure_launcher.py | keep private; ship env-only launcher |
| 19 | `[INTERNAL]` markers / methodology references in code comments (deploy paths, internal hostnames) | grep for `[INTERNAL]` markers and hostname patterns across the tree | scrub; use `[REVIEW]` placeholders |
| 20 | private compose service name `rmi-backend:8000` | config.py:111 | `RMI_BACKEND_URL` env |

| 21 | scaffold stub docstrings referencing the private monorepo path `rmi-backend/app/domains/telegram/rugmunchbot/<file>` | all 91 stubs under `src/rugmunchbot/` | neutralize to "the live production copy" before public push (kept for migration convenience while repo is private) |
| 22 | `docs/` + `MIGRATION.md` internal path references (`rmi-backend/app/domains/telegram/...`) | RUGMUNCHBOT-EXTRACTION.md, RUGMUNCHBOT-EXTRACTION-CHECKLIST.md, MIGRATION.md | these docs are migration-internal; either scrub before public tag or move to a private `docs/internal/` excluded from the public mirror |
| 23 | `bot/app/telegram_bot/` legacy tree (old standalone impl, may contain its own hardcoded IDs/refs) | `bot/app/telegram_bot/*.py` | tree is DEPRECATED + slated for removal (Stage 0/5); run gitleaks on it before ANY push, or exclude via `.gitignore`/git-rm |

**Verification:** after every Stage, run `gitleaks detect --no-git` + `rg -n "7075336557|-100[0-9]{7,}|rugmunch\.io|172\.22|x402|X-API-Key|JUPITER_|secrets-manager" src/` — expect ZERO hits.

---

## 7. Target OSS pyproject (deps from the LIVE bot)

```toml
[project]
name = "rugmunchbot"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "python-telegram-bot[job-queue]>=21.0",
    "httpx>=0.27.0",
    "structlog>=24.4.0",
    "pillow>=10.0.0",          # services/visuals.py, commands/chart.py, commands/rugmap.py
    "cachetools>=5.0.0",       # states.py, flood_control, tools/_cache.py, tools/budget.py
    "redis>=5.0.0",            # flood_control.py (optional — SQLite fallback exists)
]
[project.optional-dependencies]
scanner = []          # operator-provided adapters
billing = []          # BillingProvider implementations
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
where = ["src"]
```

> Note: live bot does NOT import matplotlib/mypy-mandatory numpy/pandas at runtime — the stale
> repo's pyproject (matplotlib/mplfinance/pandas/numpy) came from the OLD tree. Drop them.

---

## 8. Config Boundary — Private vs OSS (recommendation)

### STAYS PRIVATE (never in the OSS repo — env/secrets-manager only)
- Bot token(s) — all 3 bots
- `RMI_API_KEY` / backend X-API-Key
- **Channel IDs** (main/news/alerts/alpha/scans — the actual numeric chat IDs)
- **Owner IDs** (numeric Telegram user IDs)
- **Fee wallets** (Jupiter fee/ultra accounts — revenue)
- **Premium tiers + pricing** (USD + Stars packs — billing is the business)
- DEX referral codes (revenue attribution)
- `data/bot_users.db` (user data)
- Branding handles/URLs (rugmunch.io, X, t.me channels — operator-specific)

### SHIPS IN OSS (env-driven, empty defaults)
- All 74 product modules (§2.1)
- Env surface: `TELEGRAM_BOT_TOKEN`, `BOT_OWNER_IDS`, `BOT_CHANNELS`, `RMI_BACKEND_URL`,
  `RMI_API_KEY`, `SENTINEL_URL`, `BOT_DEX_REF_LINKS`/`RMI_REF_GMGN`/`RMI_REF_DEXTOOLS`,
  `BOT_FREE_SCANS_PER_WEEK`, `BOT_FREE_AI_MSGS_PER_WEEK`, `BIRDEYE_API_KEY`, `SEARXNG_URL`,
  `BOT_LLM_API_BASE`/`BOT_LLM_MODEL`/`BOT_LLM_API_KEY`, `BOT_DB_PATH`, `REDIS_*`, `LOG_LEVEL`
- `plugins/` protocols: `BillingProvider`, `ScanProvider`, `DataProvider`, `LLMRouter`
- Single `free` tier with tunable quotas; premium becomes feature flags, not hard tiers

> **Branding nuance [REVIEW]:** "RugMunch"/"RugMunchBot" as a *bot name* is fine for OSS (it's
> the project identity, like any FOSS project name), but **operator handles, channel links, fee
> wallets and the rugmunch.io website URL are private assets** — they become env with empty
> defaults, and the shipped README describes a neutral self-hosted deployment.

---

## 9. [REVIEW] Flags

1. **64 vs 99 modules:** `RUGMUNCHBOT-EXTRACTION.md` §2 says 64; live tree is 99 (21 `tools/`
   files added since). Checklist §1 is authoritative — update the old doc.
2. **`scanner.py` top-level `app.rag.scan_memory` + `app.domains.scanner.gmgn_enrich`:** import
   of the OSS package hard-fails until adapters exist. Stage 3 is blocking for `scanner.py`
   specifically — schedule adapters BEFORE the import-smoke test.
3. **`handlers/price.py` top-level `ScannerBridge`:** same blocker class as #2.
4. **`formatters/_base.py` REF_CODES** duplicates `config.DEX_REF_LINKS` ref codes — two sources
   of truth for the same attribution; unify on env in the rewrite.
5. **`BETA_MODE` default `true`** — in OSS default must be `false`, or free-tier quotas never
   enforce (silent billing bypass in any fork).
6. **`handlers/owner.py:375` hardcoded `7075336557`** — the live bot's owner-only surfaces are
   gated by `is_owner` (config-driven) BUT this second hardcoded guard bypasses env config; scrub.
7. **Stale tree removal timing:** `bot/app/telegram_bot/` removal is Stage 0 but the scaffold
   keeps `_ARCHIVED/` backups — do NOT delete until Stage 5 tests pass on the ported code.
8. **`data/bot_users.db`** must be excluded from any rsync/cp of the tree (it's a live user DB —
   PII: Telegram IDs, tier state, ref codes).
9. **tests in rmi-backend:** 49 files (~12.5k lines) exercise the live bot — port them, don't
   rewrite from scratch; several assert branded strings → parametrize over `BOT_*` env.

---

*Companion docs: `docs/RUGMUNCHBOT-EXTRACTION.md` (design), `docs/RUGBRAIN_RUGMUNCHBOT_OSS_PLAN.md`
(§2 RugMunchBot boundary), `MIGRATION.md` (scaffold→live step-by-step), `DEPRECATED.md` (stale tree audit).*
