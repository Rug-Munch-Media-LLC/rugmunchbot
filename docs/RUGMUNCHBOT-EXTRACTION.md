# RUGMUNCHBOT-EXTRACTION — Migration Plan

> **Status:** PLANNING (read-only analysis, 2026-08-13) · **Scope:** move the live bot from
> `rmi-backend/app/domains/telegram/rugmunchbot/` fully into this repo (`rugmunchbot`).
> **Constraint:** rmi-backend is NOT modified — this plan only documents.

## 1. Current State — Two Codebases, One Live Bot

| Location | Role | Modules | Notes |
|---|---|---|---|
| `rmi-backend/app/domains/telegram/rugmunchbot/` | **LIVE** (systemd `rugmunchbot.service`) | 64 `.py` | Modern layout: `handlers/` (27 files incl. `callback/`, `chat_ai/`), `commands/` (9), `formatters/` (4), `services/visuals.py`, `scanner.py`, `db.py`, `config.py` |
| `rugmunchbot/bot/app/telegram_bot/` | **STALE** standalone | ~30 `.py` | Old architecture: `admin_ai|admin_bot|admin_deploy|admin_docker|admin_monitor|admin_ops|admin_terminal.py`, `referrals.py`, `guardrails.py`, `services/{llm,scanner,image_gen}.py`, `handlers/{market,scan,menu,analyze,alerts,clever}.py` |

**NOT a dup.** `diff -rq` shows the trees share only `bot.py`, `config.py`, `db.py`, `keyboards.py`
(and those differ in content). The standalone is an older, independent implementation.

**Doc contradiction (must fix):** `README.md` says "archived"; `ARCHIVED.md` says "REVERSED —
top tier, restored". TODO.md (2026-07-31) flags this: "Two codebases, no sync. Pick one."

**Tests:** repo's 86 tests exercise the STALE standalone code (dead code). Live bot has **zero** tests.

**VCS:** repo is healthy — 4 remotes (forgejo/codeberg/gitlab/origin), CI (`.forgejo/`),
standards files, conventional-commit history. `.env` untracked (only `.env.example` tracked). ✅

## 2. Module Inventory (live bot, 64 modules)

```
rugmunchbot/                         (inside rmi-backend today)
├── bot.py            entry (23KB)   ├── scanner.py         scan engines (28KB)
├── config.py         env config (24KB, self-contained, no app.* imports)
├── db.py             SQLite layer (31KB, self-contained — NOT app.db)
├── admin_state.py / ai_chat.py / alert_worker.py / anti_abuse.py /
│   broadcast_worker.py / daily_context.py / data_resolver.py /
│   flood_control.py / keyboards.py / prompts.py / states.py / ta_indicators.py
├── commands/   chart events portfolio premium premium_extra premium_v2 rugmap simulate smartmoney
├── handlers/   account alerts api_access ask callback/{_admin,_inline,_run_cards} chat chat_ai/{_helpers}
│               defi error groups holders inline investigate market message owner payments prediction
│               price report research scanning sentiment trace watchlist webhook
├── formatters/ _base _sentinel _wallet
└── services/  visuals
```

## 3. Dependency List — What Couples the Bot to rmi-backend

Every external `app.*` import found (46 files touch at least one):

| Import | Files | Type | Decoupling |
|---|---|---|---|
| `app.core.logging.get_logger` | **46** | top-level | Extract 10-line shim; mechanical rewrite |
| `app.core.redis.get_redis_async` | 3 (admin_state, alert_worker, daily_context) | top-level | Injectable/optional client |
| `app.rag.scan_memory` (schedule_scan_memory_index, should_index_scan) | 1 (scanner.py) | top-level | Backend HTTP call or callback |
| `app.domains.scanner.engines.launchpad_registry.detect_launchpad` | 1 (formatters/_sentinel.py) | **lazy** | Optional plugin/HTTP |
| `app.domains.scanner.engines.event_correlation.correlate_news_with_price` | 1 (commands/events.py) | **lazy** | Optional plugin/HTTP |
| `app.domains.scanner.unified.scoring.BLUECHIP_ADDRESSES` | 1 (formatters/_sentinel.py) | **lazy** | Static data or HTTP |
| `app.domains.telegram.headless` (get_headless_client, _client_instance) | 4 (bot, scanner, handlers/chat_ai, _inline) | lazy | Extract or HTTP |
| `app.domains.telegram.voice_chat` (get_voice_chat_status, create/end) | 2 (commands/events, bot) | lazy | Extract or HTTP |
| `app.domains.telegram.scanner_bridge.ScannerBridge` | 1 (scanner.py) | top-level | HTTP client (already backend API) |
| `app.domains.telegram.access_guard` | 3 (groups, owner, callback/_admin) | top-level | Extract sibling module |

**NOT coupled:** `app.db` (bot has own local `db.py`), `app.config`/`app.core.config` (bot's
`config.py` is self-contained), `app.domains.telegram.router` (workers run standalone via systemd).

## 4. Target Repo Structure (rugmunchbot)

```
rugmunchbot/
├── bot/                    ← package root (keep name to minimize churn)
│   ├── app/telegram_bot/   ← 64 live modules copied verbatim
│   ├── support/            ← NEW: extracted shims (logging, redis, scanner_bridge client)
│   └── integrations/       ← NEW: optional headless / voice_chat / scan_memory / launchpad stubs
├── tests/                  ← NEW: tests for LIVE code (replace stale 86)
├── pyproject.toml          ← deps: python-telegram-bot, httpx, redis, matplotlib/mplfinance (optional)
└── docs/, .forgejo/, standards files (already present)
```

Import rewrite pattern (mechanical, ast-grep):
`from app.core.logging import get_logger` → `from rugmunchbot.support.logging import get_logger`

## 5. Staged Migration Order

1. **Stage 0 — Truth sync (no code):** fix README/ARCHIVED contradiction; delete/archive stale
   `bot/app/telegram_bot/` old modules; decide standalone `tests/` fate.
2. **Stage 1 — Copy live tree** into `bot/app/telegram_bot/`; add `support/` with `logging.py`
   shim (46-file rewrite), `redis.py` (3 files), `scanner_bridge.py` HTTP client.
3. **Stage 2 — Optional integrations** behind env-gated lazy imports: headless, voice_chat,
   scan_memory, launchpad_registry, event_correlation, BLUECHIP_ADDRESSES → `integrations/`
   with graceful no-op fallbacks (matches existing lazy-import pattern).
4. **Stage 3 — Config/DB hardening** while green: local `config.py` env surface, SQLite WAL
   (TODO.md CRITICAL/HIGH items — NameError, `cmd_ta`, dead API calls, usage-race).
5. **Stage 4 — Test parity:** port/respx-mock external APIs; coverage gate ≥60% on live code.
6. **Stage 5 — Cutover:** systemd unit → this repo venv; rmi-backend keeps thin re-export
   compat (`app.domains.telegram.rugmunchbot` → import from rugmunchbot) for one release cycle,
   then remove.

## 6. Stays in rmi-backend vs Moves

| Stays in rmi-backend | Moves to rugmunchbot |
|---|---|
| `app/core/` (logging, redis — shared platform) | All 64 bot modules |
| `app/rag/`, `app/domains/scanner/engines/` (products) | `support/` shims + `integrations/` adapters |
| `app/domains/telegram/headless|voice_chat|access_guard|scanner_bridge` (siblings — OPTIONAL: may move with bot) | `pyproject.toml`, systemd unit, tests, docs |

## 7. Risks / [REVIEW] Flags

- [REVIEW] `scanner.py` imports `app.rag.scan_memory` **top-level** — will hard-fail on import
  until integration stub exists; treat Stage 2 as blocking for scanner module, not whole bot.
- [REVIEW] Sibling modules (`headless`, `voice_chat`, `access_guard`, `scanner_bridge`) live in
  `app/domains/telegram/` but OUTSIDE the bot dir — decide move-with vs HTTP-bridge per module.
- [REVIEW] Repo `README`/`ARCHIVED.md` contradiction must be resolved before Stage 1 (history confusion).
- [REVIEW] Standalone old code + 86 dead tests will linger unless explicitly purged in Stage 0.
- 46-file `logging` rewrite is low-risk but must land as ONE commit + mypy/ruff gate.
