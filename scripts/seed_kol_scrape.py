#!/usr/bin/env python3
"""Seed scraped KOL records into the bot DB + Qdrant (KOL ingest).

Reads the pryscraper KOL leaderboard spider output (a JSON list of
normalized records, or the ``{"records": [...]}`` wrapper the spider emits)
and runs :func:`rugmunchbot.data.kol_ingest.ingest_scraped` to:

* dedup against the existing ~1576-wallet / ~1246-handle registry,
* persist NEW identities to ``kol_identities`` + ``kol_aliases`` and attach
  their wallets via the existing DB accessors,
* embed each new identity into Qdrant ``kol_identities_v1`` (2560-dim),
* flag serial-deployer / shill-group candidates into ``baddies.json``.

Additive and idempotent — re-running never un-verifies an identity and never
duplicates a wallet / alias / baddy.

Usage:
    .venv/bin/python scripts/seed_kol_scrape.py <scraped.json> [--no-embed]

    <scraped.json>  path to the pryscraper kol_leaderboards output (JSON).
    --no-embed      skip the Qdrant embedding step (DB write only).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _load_records(path: str) -> list[dict[str, Any]]:
    """Load scraped KOL records from a spider output file.

    Accepts either a bare JSON list of records or the ``{"records": [...]}``
    wrapper the ``fetch_kol_leaderboards`` spider emits. Empty on error.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"failed to read {path}: {e}", file=sys.stderr)
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict) and isinstance(data.get("records"), list):
        return [r for r in data["records"] if isinstance(r, dict)]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed scraped KOL records into the bot DB + Qdrant"
    )
    parser.add_argument("scraped", help="Path to the pryscraper kol_leaderboards output JSON")
    parser.add_argument(
        "--no-embed", action="store_true", help="Skip Qdrant embedding (DB write only)"
    )
    args = parser.parse_args()

    import asyncio

    from rugmunchbot.data.kol_ingest import ingest_scraped

    records = _load_records(args.scraped)
    if not records:
        print("no records to ingest")
        return 1

    summary = asyncio.run(ingest_scraped(records, embed=not args.no_embed))
    print("KOL scrape ingest summary:")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
