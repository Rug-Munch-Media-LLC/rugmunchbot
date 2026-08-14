# rugmunchbot — Production TODO
**Last audit:** 2026-07-31 · **Status:** Live & working, minor breakage · **Grade:** B-

## 🔴 BLOCKING

- [ ] **CRITICAL: Fix 3 broken backend API calls** — `scanner.py:241`: `/api/v1/databus/wallet/{address}` → 404 **[SUPERSEDED: these files are dead standalone code — fixed correctly in the canonical live copy; see DEPRECATED.md]** (only `/wallet/{address}/tokens` + `/pnl` exist). `trace.py:134`: `/api/v1/databus/cross_chain/{address}` → 404 (real route: `/bitquery/cross-chain/`). `webhook.py:236`: `/api/v1/scanner/webhooks/register` → 404 (real route: `POST /api/v1/scanner/webhooks`). Wallet forensics, cross-chain tracing, and webhook registration silently fail.
- [ ] **CRITICAL: Fix `telegram.error.TelegramError` NameError** **[SUPERSEDED: dead standalone code — canonical copy unaffected; see DEPRECATED.md]** — `payments.py:150` and `message.py:116`: catch `telegram.error.TelegramError` but `telegram` is never imported as a module name (only `from telegram import ...`). Any exception in these paths raises NameError in except block.
- [ ] **CRITICAL: Fix `cmd_ta` NameError** **[SUPERSEDED: dead standalone code — canonical copy unaffected; see DEPRECATED.md]** — `scanning.py:325`: references `target` but variable is `ctx.args[0]`, never assigned to `target`. NameError on every `/ta` call.

## 🟠 HIGH

- [ ] Fix standalone repo code — `_report_to_scanner()` POSTs to `/api/v1/scanner/scan` which doesn't exist. Remove or fix.
- [ ] Fix `alert_hooks.py` dead code — send_alert/send_scanner_alert/send_system_alert have zero callers. Scanner alerts never reach Telegram despite docs claiming they do.
- [ ] Fix `increment_usage()` race — SQLite read-modify-write without transaction. Concurrent scans overshoot limits.
- [ ] Fix `busy_timeout` not set on SQLite — concurrent handler writes throw "database is locked".
- [ ] Fix unbounded in-memory dicts — `_alert_last_sent` and `_pending_deep_scans` never pruned.
- [ ] Fix Stars payment verification — `successful_payment` never cryptographically verifies Stars amount vs pack price.
- [ ] Fix subscription hardcoded 30 days — no renewal logic, no subscription state machine.
- [ ] Fix `precheckout` no idempotency guard — any prefix-valid payload accepted, replay possible.
- [ ] Fix ban check only in `message.py` — banned users can still use ALL slash commands.
- [ ] Fix `drop_pending_updates=True` — drops updates during restarts. JobQueue without persistence.

## 🟡 MEDIUM

- [x] **Decide archive story (2026-08-14)** — canonical = live bot in `rmi-backend/app/domains/telegram/rugmunchbot/`. This repo = future OSS home via `docs/RUGMUNCHBOT-EXTRACTION.md`. Current `bot/app/telegram_bot/` tree = deprecated (see `DEPRECATED.md`). Do NOT fix its defects; it is replaced by the extraction, not retained.
- [ ] Fix error handler noise — "Something went wrong" for any exception including benign `BadRequest: message is not modified`.
- [ ] Fix `cmd_quick` and `$TOKEN` lookups bypass rate limits and scan quota.
- [ ] Fix hardcoded owner ID fallback `7075336557`.
- [ ] Fix hardcoded channel IDs in config.
- [ ] Fix `_fetch_prices_batch` — one invalid address → DexScreener 400 for entire batch.
- [ ] Fix `get_user_tier` closes/re-opens connection mid-function.
- [ ] Fix `deep_scan` payload flow bypasses tier checks (anyone can trigger `handle_callback` with `deep_` prefix).
- [ ] Fix `referrals` — standalone has referral module; live bot has inline referral in scanning.py but no standalone ref counter.
- [ ] Fix `rugmunchbot/.env` secret hygiene in repo.

## 🟢 LOW

- [ ] Add per-second token bucket rate limiting (currently single 3s global cooldown TTLCache)
- [ ] Add SQLite WAL checkpointing
- [ ] Add persistence for JobQueue (alerts lost on restart)
- [ ] Switch to webhook mode for multi-worker scaling
- [ ] Fix `requirements.lock` outdated vs `requirements.txt`
- [ ] Add renovate.json (missing)
- [ ] Fix mypy CI gate (`--strict` + `continue-on-error` — fake gate)

## Tests

- [ ] **Zero tests for live bot code.** 86 tests in standalone repo test dead code.
- [ ] Add tests for `bot.py` command registration, `scanner.py` engines, all handlers (scanning, alerts, payments, callback, inline, message AI)
- [ ] Add tests for `states.py` rate limiting, `formatters.py`, `keyboards.py`
- [ ] Add respx-mocked tests for external APIs (DexScreener, GoPlus, Honeypot, backend)
- [ ] Make coverage blocking (currently 22% with `|| echo "non-blocking"` swallow)
- [ ] Target 60%+ coverage on live bot
