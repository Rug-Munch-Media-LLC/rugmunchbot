"""
RugMunchBot — self-hostable Telegram crypto intelligence bot.

OSS package root. This repo is the designated public home of the bot that
lives today in the private monorepo at
``rmi-backend/app/domains/telegram/rugmunchbot/``.

MIGRATION STATUS: SCAFFOLD. The live code has NOT been copied here yet.
Each module below is a stub pointing at its canonical source file; follow
``docs/RUGMUNCHBOT-EXTRACTION-CHECKLIST.md`` Stages 1-4 to port it.

Canonical source map (rmi-backend paths → this package):
    rugmunchbot/bot.py            ← app/domains/telegram/rugmunchbot/bot.py
    rugmunchbot/config.py         ← .../config.py          (env-rewritten, private bits stripped)
    rugmunchbot/db.py             ← .../db.py              (SQLite, no billing)
    rugmunchbot/scanner.py        ← .../scanner.py         (ScanProvider protocol)
    rugmunchbot/handlers/         ← .../handlers/          (28 modules)
    rugmunchbot/commands/         ← .../commands/          (10 modules)
    rugmunchbot/formatters/       ← .../formatters/        (5 modules)
    rugmunchbot/tools/            ← .../tools/             (21 modules)
    rugmunchbot/services/         ← .../services/visuals.py
    rugmunchbot/support/          ← .../support/logging.py (self-contained structlog shim)
    rugmunchbot/plugins/          ← NEW protocol seams (billing, scanner, data, llm)
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
