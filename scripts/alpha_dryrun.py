#!/usr/bin/env python3
"""Alpha-channel dry-run — assemble what @rmialpha would post, no send.

Verification harness for the whale + smart-money + KOL-trades wiring:
fetches the same content the scheduler jobs use (ClickHouse whales,
smart-money feed, launches, KOL trades via ApiPass-with-SearXNG-fallback)
and prints the formatted alpha digest + the 30-min whale-watch post without
sending anything to Telegram.

Usage:
    RMI_BOT_DIR=/srv/work/repos/rugmunchbot \
    .venv/bin/python scripts/alpha_dryrun.py [--whales | --alpha | --all]

Exit 0 even when a source is quiet — the channels fail open by design; the
printed digest shows exactly what content exists right now.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

# Allow running from a checkout without installing the package.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


async def dry_run_alpha() -> int:
    """Assemble the hourly alpha digest (whale+smart+launch+kol) and print it."""
    from rugmunchbot.channels.content import fetch_alpha_content
    from rugmunchbot.channels.formatters import format_alpha_post

    content = await fetch_alpha_content(
        chain="solana",
        whale_limit=3,
        smart_limit=5,
        launch_limit=5,
        early_entry_limit=2,
        kol_limit=3,
    )
    print("═" * 60)
    print("ALPHA DIGEST (channel_alpha) — dry-run, nothing sent")
    print("═" * 60)
    counts = {
        "whale_moves": len(content["whale_moves"]),
        "smart_money": len(content["smart_money"]),
        "launches": len(content["launches"]),
        "kol_trades": len(content.get("kol_trades", [])),
    }
    print("source counts:", counts)
    if sum(counts.values()) == 0:
        print("(all sources quiet — channels fail open by design)")
        return 0
    try:
        post = format_alpha_post(
            content["whale_moves"],
            content["smart_money"],
            content["launches"],
            kol_trades=content.get("kol_trades", []),
        )
    except Exception as exc:
        print(f"(formatter failed: {exc})")
        return 1
    print(post)
    print()
    return 0


async def dry_run_whales() -> int:
    """Assemble the 30-min whale-watch post and print it."""
    from rugmunchbot.channels.scheduler._posts_whales import build_whale_post

    print("═" * 60)
    print("WHALE WATCH (channel_whale_alerts) — dry-run, nothing sent")
    print("═" * 60)
    try:
        post = await build_whale_post()
    except Exception as exc:
        print(f"(whale builder failed: {exc})")
        return 1
    if not post:
        print("(no fresh whale moves — quiet window)")
        return 0
    print(post)
    print()
    return 0


async def dry_run_kol_shame() -> int:
    """Assemble KOL report cards + hall-of-shame preview (store read only)."""
    from rugmunchbot.channels.kol_hall_of_shame import (
        KolCall,
        compute_report_card,
        format_hall_of_shame_post,
        rank_hall_of_shame,
    )
    from rugmunchbot.db import get_kol_scores, list_kol_calls

    print("═" * 60)
    print("KOL HALL OF SHAME — dry-run (store read only, no alerts sent)")
    print("═" * 60)
    cached = {str(r["handle"]): r for r in get_kol_scores(limit=100)}
    cards = []
    for row in cached.values():
        rows = list_kol_calls(handle=str(row["handle"]), limit=100)
        if not rows:
            continue
        calls = [KolCall.from_row(r) for r in rows]
        wallet = next((c.wallet_address for c in calls if c.wallet_address), "")
        cards.append(compute_report_card(calls, wallet_address=wallet))
    hall = rank_hall_of_shame(cards, limit=5)
    if not hall:
        print("(no KOLs in the hall yet — run the sweeper to populate)")
        return 0
    print(format_hall_of_shame_post(hall))
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--which", choices=("all", "alpha", "whales", "kol"), default="all")
    args = parser.parse_args()

    async def _run() -> int:
        rc = 0
        if args.which in ("all", "alpha"):
            rc |= await dry_run_alpha()
        if args.which in ("all", "whales"):
            rc |= await dry_run_whales()
        if args.which in ("all", "kol"):
            rc |= await dry_run_kol_shame()
        return rc

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
