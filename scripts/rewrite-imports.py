#!/usr/bin/env python3
"""Rewrite rugmunchbot internal imports from the rmi-backend namespace to the
standalone package name.

LIVE package lives at ``app.domains.telegram.rugmunchbot`` (rmi-backend).
Phase 1 makes the repo copy importable as the top-level ``rugmunchbot``
package: internal references (``from app.domains.telegram.rugmunchbot.X
import Y``, ``import app.domains.telegram.rugmunchbot``, string refs such as
logger names / sys.modules lookups / importlib module strings) are rewritten
to ``rugmunchbot.*``.

PLATFORM imports are intentionally UNTOUCHED — ``app.config``, ``app.core``,
``app.domains.news_ingest``, ``app.domains.telegram.access_guard/headless``,
``app.llm_config``, ``app.osint`` still resolve from rmi-backend via
PYTHONPATH until Phase 2 unwinds them.

Idempotent (rewriting twice is a no-op). Applied automatically by
scripts/sync-from-live.sh after every rsync, so a fresh live pull always
produces the runnable repo layout.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "src" / "rugmunchbot"
OLD = "app.domains.telegram.rugmunchbot"
NEW = "rugmunchbot"


def rewrite() -> int:
    changed = 0
    for p in sorted(ROOT.rglob("*.py")):
        raw = p.read_text(encoding="utf-8")
        if OLD not in raw:
            continue
        updated = raw.replace(OLD, NEW)
        p.write_text(updated, encoding="utf-8")
        changed += 1
    return changed


if __name__ == "__main__":
    changed = rewrite()
    print(f"rewrote imports in {changed} file(s) under {ROOT}")
    sys.exit(0)
