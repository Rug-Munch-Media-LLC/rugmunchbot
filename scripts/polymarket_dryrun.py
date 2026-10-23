#!/usr/bin/env python3
"""Dry-run one Polymarket content cycle and print the cards (no publishing).

Usage::

    python scripts/polymarket_dryrun.py            # default settings, dry run
    POLYMARKET_MAX_MARKETS=2 python scripts/polymarket_dryrun.py

The engine is read-only; this script cannot trade, sign, or post.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from rugmunchbot.polymarket import PolymarketData, PolySettings, build_drafts


async def main() -> int:
    settings = PolySettings.from_env()
    drafts = await build_drafts(PolymarketData(), settings)
    if not drafts:
        print("no cards this cycle (filters, dedupe, or API unavailable)")
        return 0
    for draft in drafts:
        print("=" * 72)
        print(draft.text)
        print(f"[dry_run={draft.dry_run} key={draft.dedupe_key}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
