#!/usr/bin/env python3
"""Check the competitor-source manifest for stale entries (no network).

Usage: python3 scripts/competitors_refresh.py [--check] [--today YYYY-MM-DD]
Exit 1 under --check when any source is older than ``refresh_days``.
Re-reading the URL is a human/pry step; this only proves the clock is honest.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

MANIFEST = Path(__file__).resolve().parent.parent / "data" / "competitors" / "sources.json"


def _age_days(accessed: str, today: date) -> int:
    return (today - date.fromisoformat(accessed)).days


def stale_sources(manifest: dict, today: date) -> list[dict]:
    """Sources whose accessed date exceeds their refresh window."""
    stale = []
    for src in manifest.get("sources", []):
        age = _age_days(src["accessed"], today)
        if age > int(src.get("refresh_days", 30)):
            stale.append({**src, "age_days": age})
    return stale


def _report(manifest: dict, today: date) -> int:
    stale = stale_sources(manifest, today)
    total = len(manifest.get("sources", []))
    print(f"{total - len(stale)}/{total} sources fresh as of {today.isoformat()}")
    for src in stale:
        print(f"STALE {src['id']} ({src['age_days']}d > {src['refresh_days']}d) {src['url']}")
    return 1 if stale else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 when stale")
    parser.add_argument("--today", default=None, help="override today (YYYY-MM-DD)")
    args = parser.parse_args(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    today = date.fromisoformat(args.today) if args.today else date.today()
    rc = _report(manifest, today)
    return rc if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
