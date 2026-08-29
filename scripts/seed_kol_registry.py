#!/usr/bin/env python3
"""Seed the KOL Scoreboard registry from real fleet data.

Loads the vendored sources (``kol_registry_seed``): 504 influential CT
accounts + 31 evidence-backed scammers + curated CT extras → ~565 canonical
identities, plus the 945-wallet knowledgebase and 24 documented rug/dump
calls (seeded as loss calls so report cards start with real win/loss).

Idempotent — safe to re-run; seeding never un-verifies an identity and
never duplicates wallets/calls.

Usage:
    .venv/bin/python scripts/seed_kol_registry.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def main() -> int:
    from rugmunchbot.db import (
        apipass_usage_summary,
        kol_registry_count,
        list_kol_calls,
        seed_kol_registry,
        seed_kol_rug_history,
        seed_wallet_kb,
    )
    from rugmunchbot.kol_registry_seed import (
        build_rug_history,
        build_seed_rows,
        build_wallet_kb,
        seed_stats,
    )

    stats = seed_stats()
    print(
        f"sources: {stats['known_scammers']} scammers, "
        f"{stats['influential_registry']} influential, "
        f"{stats['curated_extra']} curated extra, "
        f"{stats['wallets']} wallets, "
        f"{stats['rug_history_calls']} documented rug/dump calls"
    )

    seeded = seed_kol_registry(build_seed_rows())
    wallet_count = seed_wallet_kb(build_wallet_kb())
    rug_count = seed_kol_rug_history(build_rug_history())

    print(f"seeded identities: {seeded}")
    print(f"registry count now: {kol_registry_count()}")
    print(f"wallets attached: {wallet_count}")
    print(f"rug-history loss calls recorded: {rug_count}")
    print(f"calls in store: {len(list_kol_calls(limit=10000))}")

    usage = apipass_usage_summary()
    print(
        f"apipass budget: {usage['credits_used']:.2f}/{usage['free_allowance']:.2f} "
        f"credits used ({usage['total_calls']} calls), "
        f"{usage['calls_today']} today, ~{usage['estimated_calls_remaining']} calls remaining"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
