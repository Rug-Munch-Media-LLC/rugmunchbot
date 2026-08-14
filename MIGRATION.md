# MIGRATION — Scaffold → Live Bot

How the `src/rugmunchbot/` scaffold becomes the real bot. This is the
step-by-step companion to `docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md`
(what/why) — this file is the how. No code has been moved yet; everything
here is the execution order.

## 0. The two codebases (read first)

| Location | Role | Status |
|---|---|---|
| `rmi-backend/app/domains/telegram/rugmunchbot/` | **LIVE** production bot (99 modules) | canonical source of truth |
| `bot/app/telegram_bot/` (this repo, legacy) | older standalone implementation | **DEPRECATED** — see `bot/app/telegram_bot/DEPRECATED.md` |
| `src/rugmunchbot/` (this repo, new) | target OSS package | **SCAFFOLD** — stubs only |

The private monorepo is NOT modified by this migration. The bot is extracted
*out of* it; it keeps running from there until cutover (Stage 6).

## 1. Port order (one atomic unit per commit)

Run the rename checklist per module (grep → lazy maps → atomic change →
full suite → gitleaks) — see `docs/RUGBRAIN_RUGMUNCHBOT_OSS_PLAN.md` §3.0.

### Stage 1 — Scaffold (this state)
- [x] `pyproject.toml` → `src/` layout, live-bot deps
- [x] `src/rugmunchbot/` stubs for all 99 modules, pointing at live files
- [x] `plugins/` protocol seams (Billing, Scan, Data, LLM)
- [x] `.env.example`, README, this file

### Stage 2 — Pure surface (no backend deps)
1. `src/rugmunchbot/support/logging.py` — **already portable**, copy verbatim from live
2. `formatters/*`, `keyboards.py`, `prompts.py`, `states.py`, `ta_indicators.py`, `services/visuals.py`
3. `flood_control.py`, `anti_abuse.py`, `broadcast_worker.py`
4. `db.py`, `admin_state.py` (billing → `BillingProvider`)

### Stage 3 — Tools + data layer
5. `tools/*` (21) — `tools/birdeye.py` key resolution → env only
6. `data_resolver.py` → provider-injected; `tools/registry.py` reads `RMI_BACKEND_URL`
7. `daily_context.py`, `ai_chat.py` → chat API via env + local LLM fallback
8. `scanner.py` → `ScanProvider` protocol (sever `gmgn_enrich`/`scan_enrich`/`scan_memory`)

### Stage 4 — Commands + handlers + entry
9. `commands/*` (10)
10. `handlers/*` (28) — split `scanning.py` (58KB) into `scan_flow` + `scan_backend`
11. `handlers/callback/*`, `handlers/chat_ai/*` → verbatim + `LLMRouter`
12. `bot.py` + new env-only `secure_launcher.py`

### Stage 5 — Tests
13. Port the 49 live test files from the monorepo (`tests/domains/test_rugmunchbot_*.py`,
    `tests/test_bot_*.py`, `tests/test_scan_*.py`, `tests/test_chat_ai_*.py`,
    `tests/test_ref_links_chart.py`, `tests/test_sentinel_rename.py`, ...)
14. Delete the 9 stale test files (`tests/test_analyze_handler.py`, ...)
15. Coverage gate ≥60%, `mypy --strict`, `ruff`, `bandit`, `gitleaks` in CI

### Stage 6 — Cutover (private-side, one release cycle)
16. systemd unit points at this repo's venv (`python -m rugmunchbot`)
17. Monorepo leaves a thin `app.domains.telegram.rugmunchbot → rugmunchbot` re-export shim
18. Remove shim after one release; tag v0.1.0

## 2. Import rewrite pattern

| Live (private) | OSS |
|---|---|
| `from app.domains.telegram.rugmunchbot.X import Y` | `from rugmunchbot.X import Y` |
| `from app.core.logging import get_logger` | `from rugmunchbot.support.logging import get_logger` (already the live pattern) |
| `from app.domains.telegram.scanner_bridge import ScannerBridge` | `from rugmunchbot.tools.registry import backend_json_get` |
| `from app.rag.scan_memory import ...` | no-op/HTTP adapter via `ScanProvider` |

## 3. What NOT to port (private)

See checklist §2.2 (stays) and §6 (scrub list). Highlights:

- secrets-manager paths, secret files, `X-API-Key` values
- owner ID `7075336557` (and any numeric Telegram IDs)
- channel IDs `-100...`
- Jupiter fee/ultra wallet addresses
- DEX ref codes (`ref=rugmunch`, `start=x5pyi0`, `?ref=crm...`)
- Stars pricing tiers / top-up / scan packs
- `data/bot_users.db` (live user data)
- branding URLs (operator handles, website, channel links)

Before any public push: `gitleaks detect --no-git` must pass and
`rg -n "7075336557|-100[0-9]{7,}|rugmunch\\.io|172\\.22|x402|X-API-Key|JUPITER_|secrets-manager" src/` (plus secrets-manager path references)
must return nothing.

## 4. Verification gates (each stage)

```bash
ruff check src/ tests/
mypy src/rugmunchbot/
python -m pytest tests/ -q
gitleaks detect --no-git
```
