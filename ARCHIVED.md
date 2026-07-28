# ARCHIVED — Standalone bot code

**Date:** 2026-07-28
**Decision:** Archive standalone `bot/app/telegram_bot/`

## What happened

The standalone Telegram bot code at `bot/app/telegram_bot/` has been **archived** (moved to
`bot/app/telegram_bot/_ARCHIVED/`). This code is **dead**: the systemd service
(`/etc/systemd/system/rugmunchbot.service`) runs the embedded version from
**rmi-backend** at `app/domains/telegram/rugmunchbot/`.

## Why

- Two bot codebases existed — this standalone repo and the embedded version in rmi-backend
- The standalone had diverged: its own `db.py`, `config.py`, admin ops, all different from
  the embedded version
- Live secrets in `.env` (now safely gitignored and archived)
- Stale `.bak` files, dead commands, a second admin-bot systemd unit file
- TRIAGE.md already recorded the decision to "MERGE into rmi-backend then ARCHIVE"

## What's preserved

| Path | Status |
|------|--------|
| `bot/app/telegram_bot/_ARCHIVED/` | All 12 source files + `.bak` files + `.env` + `Dockerfile` + `rmi-admin-bot.service` |
| `tests/` | Valuable test fixtures and smoke tests — kept |
| `Makefile` | Updated to target `tests/` only |
| `README.md` | New, explains current state |

## What was removed

- All 12 Python source files archived to `_ARCHIVED/`
- 4 `.bak` backup files archived
- 1 `.env` with live secrets archived (gitignored, never tracked)
- CI workflow updated (removed dead `backend/` references)
- Makefile updated to only run tests
