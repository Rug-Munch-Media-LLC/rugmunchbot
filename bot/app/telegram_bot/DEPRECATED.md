# DEPRECATED — Stale Standalone Bot Code (superseded 2026-08-14)

> **Status:** SUPERSEDED — do not run, do not deploy, do not port features from this tree.
> **Canonical live bot:** `rmi-backend/app/domains/telegram/rugmunchbot/` (systemd `rugmunchbot.service`).
> **Planned replacement:** this repo becomes the OSS home via `docs/RUGMUNCHBOT-EXTRACTION.md`.

## Why this tree is deprecated

This directory (`bot/app/telegram_bot/`) is an **older, separate implementation** of the RugMunch
Telegram bot. It is **not** the code running in production:

- The live bot runs from `rmi-backend/app/domains/telegram/rugmunchbot/` via
  `/etc/systemd/system/rugmunchbot.service` → `secure_launcher` (confirmed by the running
  `python -m app.domains.telegram.secure_launcher rugmunchbot` process).
- This tree is **not running**, has diverged architecturally (monolithic `bot.py` ≈ 6,000 lines vs
  the live bot's modular `handlers/` + `commands/` + `tools/` layout), and contains known defects.
- Per `docs/RUGMUNCHBOT-EXTRACTION.md` Stage 0, this stale tree is slated for **archive/removal**
  and replacement by the extracted live code. **Do not fix defects here — the code is being replaced,
  not retained.**

## Known defects in this tree (audited 2026-08-14 — intentionally NOT fixed)

These are documented here so nobody is misled into thinking this tree is healthy. They are already
correct in the canonical live copy.

| # | Defect | Location (this tree) | Canonical copy (rmi-backend) |
|---|--------|----------------------|------------------------------|
| 1 | **Bad wallet endpoint** — `GET /api/v1/databus/wallet/{address}` 404s; correct routes are `/wallet/{address}/tokens` and `/wallet/{address}/pnl` | `bot.py:764` (`analyze_wallet`), `handlers/clever.py:871` (`cmd_wallet_v2`) | Uses `/wallet/{address}/tokens` (scanner.py, handlers/investigate.py, handlers/trace.py, commands/portfolio.py) + `/pnl` (tools/security.py) |
| 2 | **Duplicate `/chart` + `/map` registration** — first registration wins in python-telegram-bot, so the second pair is dead | `bot.py:5975–5976` registers `cmd_chart_v8`/`cmd_map_v8`, then `bot.py:5990–5991` registers `cmd_chart`/`cmd_map` again | Single registration via `_reg(app, "chart", cmd_chart)` (bot.py:413) and `_reg(app, "rugmap", cmd_rugmap)` (bot.py:417) |
| 3 | **Blocking matplotlib/pandas at import time** — `import matplotlib`, `pandas` at module top level of the image service; `matplotlib.use("Agg")` forces a heavy import whenever the module loads | `services/image_gen.py` (top-level imports; only `mplfinance` is optional) | `services/visuals.py` uses Pillow with lazy `from PIL import ...` inside functions — no matplotlib/pandas at all |

**Why the fixes were skipped:** The task instruction is to fix defects *"IF it will remain"*.
This tree **will not remain** — `docs/RUGMUNCHBOT-EXTRACTION.md` Stage 0 archives/removes it and the
canonical live copy already implements the correct behavior. Fixing dead code that is scheduled for
removal would create false confidence and waste effort. The defects are preserved here as an audit
record only.

## What to do instead

1. Treat `rmi-backend/app/domains/telegram/rugmunchbot/` as the **only** source of truth.
2. To make RugMunchBot OSS/standalone, follow `docs/RUGMUNCHBOT-EXTRACTION.md` — it extracts the
   LIVE code into this repo (this repo is the designated future home; this stale tree is not it).
3. Do not copy files from this tree into the live bot — they are an older architecture.

## History note

- 2026-07-28: bot was incorrectly archived to `_ARCHIVED/`, then decision REVERSED (see `ARCHIVED.md`).
- 2026-08-13: `docs/RUGMUNCHBOT-EXTRACTION.md` written — plans extraction of the LIVE bot into this repo.
- 2026-08-14: This DEPRECATED.md reconciles the contradiction: repo = future OSS home, this tree = stale.

---

## Update (2026-08-14, extraction scaffold)

The OSS extraction scaffold now lives at `src/rugmunchbot/` (see
`README.md`, `MIGRATION.md`, and `docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md`).
This legacy tree is superseded by BOTH the live monorepo copy AND the new
scaffold. It is preserved here only as history and will be removed in
extraction Stage 0/5 once the ported code passes tests. Do not port features
from this tree into the scaffold — port from the live copy.
