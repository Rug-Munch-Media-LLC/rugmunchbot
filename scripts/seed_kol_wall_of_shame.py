#!/usr/bin/env python3
"""Seed the KOL Wall of Shame list + scraped KOL wallets into the bot DB.

Additive to the canonical seed (scripts/seed_kol_registry.py): loads
``data/kol/wall_of_shame.json`` (name-and-shame: handle + wallet + evidence)
and ``data/kol/scraped_kol.json`` (new kolscan/gmgn/mwayers KOL wallets) and
persists them via the existing identity / wallet-KB accessors. Never
un-verifies an identity and never fabricates a wallet.

Idempotent. Usage:
    .venv/bin/python scripts/seed_kol_wall_of_shame.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def main() -> int:
    from rugmunchbot.db import (
        attach_kol_wallet,
        get_kol_identity,
        register_alias,
        upsert_kol_identity,
    )
    from rugmunchbot.kol_registry_seed import build_scraped, build_wall_of_shame

    wall_of_shame = build_wall_of_shame()
    scraped = build_scraped()
    wall_of_shame_seeded = 0
    wallets_attached = 0

    for b in wall_of_shame:
        handle = (b.get("handle") or "").strip()
        if not handle:
            continue
        existing = get_kol_identity(handle)
        notes = f"WALL OF SHAME: {b.get('category')} ({b.get('severity')})."
        if b.get("group"):
            notes += f" Group: {b['group']}."
        if b.get("evidence_url"):
            notes += f" EVIDENCE: {b['evidence_url']}"
        upsert_kol_identity(
            kol_id=handle,
            name=(b.get("name") or handle).strip(),
            screen_name=(
                existing["screen_name"] if existing else (b.get("name") or handle)
            ).strip(),
            category=(b.get("category") or "scammer"),
            source="wall_of_shame_intel",
            verified=bool(existing and existing.get("verified")),
            risk_level=(b.get("risk_level") or "high"),
            scammer_group=(b.get("group") or ""),
            notes=notes,
        )
        register_alias(handle, b.get("name") or handle, is_current=True)
        if b.get("wallet") and attach_kol_wallet(
            handle,
            b["wallet"],
            chain="solana",
            source="wall_of_shame_intel",
            evidence_url=b.get("evidence_url") or "",
            confidence="strong_heuristic",
        ):
            wallets_attached += 1
        wall_of_shame_seeded += 1

    for r in scraped:
        handle = (r.get("handle") or "").strip().lstrip("@")
        address = (r.get("address") or "").strip()
        if not handle or not address:
            continue
        upsert_kol_identity(
            kol_id=handle,
            name=(r.get("name") or handle).strip(),
            screen_name=handle,
            category="analyst" if r.get("source") == "kolscan_leaderboard" else "shiller",
            source=str(r.get("source") or "scrape"),
            verified=False,
            risk_level="watch",
            notes=(f"scraped from {r.get('source')}") if r.get("source") else "scraped",
        )
        register_alias(handle, handle, is_current=True)
        if attach_kol_wallet(
            handle,
            address,
            chain=str(r.get("chain") or "solana"),
            source=str(r.get("source") or "scrape"),
            evidence_url=str(r.get("evidence_url") or ""),
            confidence="probabilistic",
        ):
            wallets_attached += 1

    print(f"wall of shame seeded: {wall_of_shame_seeded}")
    print(f"scraped KOL records processed: {len(scraped)}")
    print(f"wallets attached: {wallets_attached}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
