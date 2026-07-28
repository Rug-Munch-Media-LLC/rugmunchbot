# ARCHIVED — This was a mistake

**Date:** 2026-07-28 (corrected)
**Decision:** REVERSED — standalone bot is restored and is TOP TIER

## What happened

The standalone Telegram bot code at `bot/app/telegram_bot/` was **incorrectly archived**
(moved to `bot/app/telegram_bot/_ARCHIVED/`) by a cleanup agent on 2026-07-28.
The bot IS the product — @rugmunchbot is a major part of the RugMunch platform.

## Restoration

All source files have been moved back to `bot/app/telegram_bot/`:
- `bot.py` — Main bot (scan engine, wallet forensics, command handlers)
- `config.py` — Configuration (tiers, DEX ref links, formatting)
- `db.py` — Database layer (SQLite, user management, usage tracking)
- `admin_bot.py`, `admin_ai.py`, `admin_deploy.py`, `admin_docker.py`, etc.
- `commands/investigate.py` — Investigation commands
- `referrals.py` — Verified referral codes

## What's preserved in _ARCHIVED

Only backup files and non-source artifacts remain in `_ARCHIVED/`:
- `.env` (gitignored secrets — never tracked)
- `bot.py.bak*` — Stale backup files
- `Dockerfile` — Container build (merged into rmi-backend)
- `requirements.txt` — Deps (managed via pyproject.toml)
- `rmi-admin-bot.service` — systemd unit (moved to fleet-infra)

The `_ARCHIVED/` directory is kept solely to track git history of the erroneous move.

## Current status

The standalone bot at `bot/app/telegram_bot/` is:
- ✅ Fully unarchived and active
- ✅ Scanner connection live (`_report_to_scanner()` POSTs to `/api/v1/scanner/scan`)
- ✅ All tests passing
- ✅ CI updated to test the right paths
- ✅ This is a top-tier product — NOT dead code
