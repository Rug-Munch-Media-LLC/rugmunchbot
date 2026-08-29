#!/usr/bin/env python3
"""Dry-run: build the 10-post/day main-channel quality schedule with dedup.

Channel-quality overhaul (2026-08-29) verification entry: this script runs
the SAME schedule builder the live ``channel_daily_quality`` job uses
(``rugmunchbot.channels.quality_schedule``) and prints the day's 10-post
plan — 2 fixed market slots (morning/midday) + 8 reranked quality-content
slots — with dedupe notes. It sends NOTHING.

Dedup demonstrated:
  * stable-key dedup — two candidates with the same story_ref/URL collapse
    to one (no double-post);
  * qwen3-4B embed dedup — near-identical copy collapses to one entry
    (``embed_similar_to:...`` note) when the embedder is reachable.

Rerank uses the deep Qwen3-Reranker-4B (127.0.0.1:8003). All model calls
fail open — the schedule still builds on stable-key dedup + item scores.

Usage:
    python scripts/dry_run_daily_schedule.py               # sample corpus
    python scripts/dry_run_daily_schedule.py --no-embed    # key-dedup only
    python scripts/dry_run_daily_schedule.py --no-rerank   # no model rerank
    python scripts/dry_run_daily_schedule.py --posted 3    # 3 slots used today
    python scripts/dry_run_daily_schedule.py --live        # + live RAG drafts

Exit 0 on success; the schedule is printed to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))


def _sample_corpus() -> list[dict[str, object]]:
    """Realistic fresh drafts — includes key-dup + near-embed-dup pairs."""
    now = datetime.now(UTC).isoformat()

    def d(ref: str, title: str, text: str) -> dict[str, object]:
        return {"story_ref": ref, "title": title, "text": text, "ts": now, "kind": "story"}

    return [
        d(
            "exploit-1",
            "Bridge exploit drains $190M",
            "Cross-chain bridge compromised; ~$190M drained across three chains. "
            "Funds moved to fresh wallets. Source: https://rekt.news/bridge-190m",
        ),
        d(
            "etf-1",
            "Ethereum ETF sees record inflows",
            "Ethereum spot ETFs logged a record $1.2B net inflow day, led by BlackRock. "
            "Source: https://cointelegraph.com/news/eth-etf-record-inflows",
        ),
        # Near-duplicate of etf-1 (embed dedup target).
        d(
            "etf-2",
            "ETH ETF inflows hit all-time high",
            "Ethereum exchange-traded funds saw their biggest single-day inflows ever, "
            "with $1.2B pouring in led by BlackRock's product. "
            "Source: https://coindesk.com/markets/eth-etf-record-inflows",
        ),
        d(
            "whale-1",
            "Whale moves 25,000 BTC to cold storage",
            "A long-dormant whale wallet moved 25,000 BTC (~$1.5B) to cold storage — "
            "not to an exchange. Source: https://whale-alert.io/tx/btc-25k",
        ),
        d(
            "rug-1",
            "Deployer dumps $8M liquidity from memecoin",
            "The deployer of a trending memecoin pulled $8M of liquidity hours after "
            "listing; holders left with worthless tokens. Source: https://rugmunch.io/scanner",
        ),
        d(
            "reg-1",
            "SEC charges three exchanges over market manipulation",
            "The SEC filed charges against three crypto exchanges alleging wash trading "
            "and market manipulation. Source: https://theblock.co/sec-exchanges",
        ),
        d(
            "sol-1",
            "Solana congestion: transaction failures spike 400%",
            "Solana RPCs report a 400% spike in transaction failures during peak hours; "
            "priority-fee market under strain. Source: https://decrypt.co/solana-congestion",
        ),
        d(
            "ai-1",
            "AI agents now execute 12% of DEX volume",
            "Autonomous AI trading agents account for 12% of DEX volume this quarter — "
            "up from 2% a year ago. Source: https://blockworks.ai-dex-volume",
        ),
        d(
            "cex-1",
            "Major CEX lists perpetuals for three new altcoins",
            "A top exchange listed perpetual futures for three new altcoins, adding "
            "leverage to previously spot-only assets. Source: https://binance.com/announce",
        ),
        d(
            "macro-1",
            "Fed holds rates; Powell signals September cut",
            "The Fed held rates steady; Powell signaled a likely September cut, "
            "lifting risk assets including crypto. Source: https://coindesk.com/fed-hold",
        ),
        d(
            "stable-1",
            "Stablecoin supply hits new all-time high of $190B",
            "Total stablecoin supply crossed $190B — liquidity keeps compounding on "
            "chain. Source: https://defillama.com/stablecoins",
        ),
        # Stable-key duplicate of exploit-1 (same story_ref — key dedup target).
        d(
            "exploit-1",
            "Bridge exploit drains $190M (dup story_ref)",
            "Same story pushed again under the same ref — must collapse to one post. "
            "Source: https://rekt.news/bridge-190m",
        ),
        d(
            "nft-1",
            "NFT trading volume flat despite market rally",
            "NFT trading volume stayed flat this week even as tokens rallied — "
            "retail attention remains elsewhere. Source: https://dune.com/nft-volume",
        ),
    ]


async def _live_drafts() -> list[dict[str, object]]:
    """Best-effort live RAG main drafts (fail-open → [])."""
    try:
        from rugmunchbot.channels.content import fetch_rag_drafts

        return await fetch_rag_drafts("main", limit=6)
    except Exception as exc:
        print(f"[dry-run] live RAG drafts unavailable: {str(exc)[:120]}")
        return []


async def _main(args: argparse.Namespace) -> int:
    from rugmunchbot.channels.quality_schedule import _stable_key, dedupe_by_embedding
    from rugmunchbot.channels.scheduler._posts_daily import dry_run_daily_schedule

    items: list[dict[str, object]] = list(_sample_corpus())
    if args.live:
        items.extend(await _live_drafts())

    print(
        f"[dry-run] candidates={len(items)} embed_dedup={not args.no_embed} rerank={not args.no_rerank}"
    )
    print(f"[dry-run] posted_today={args.posted} (quality slots already used)")
    print("─" * 78)

    # ── Dedup evidence (same steps the live job runs) ─────────────────────
    keys = [_stable_key(i) for i in items]
    key_dups = len(keys) - len(set(keys))
    if key_dups:
        print(f"[dedup] stable-key: {key_dups} duplicate story_ref/url dropped (no double-post)")
    if not args.no_embed:
        unique: list[dict[str, object]] = []
        seen: set[str] = set()
        for i in items:
            k = _stable_key(i)
            if k in seen:
                continue
            seen.add(k)
            unique.append(i)
        _, embed_notes = await dedupe_by_embedding(unique)
        if embed_notes:
            print(f"[dedup] qwen3-4B embed: {len(embed_notes)} near-duplicate copy collapsed")
            for _key, note in list(embed_notes.items())[:6]:
                print(f"        · {note}")
        else:
            print("[dedup] qwen3-4B embed: no near-duplicate copy detected (or embedder timed out)")

    plan = await dry_run_daily_schedule(
        items,
        posted_today=args.posted,
        run_embed_dedup=not args.no_embed,
        run_rerank=not args.no_rerank,
    )
    print(plan)
    print("─" * 78)

    filled = sum(1 for line in plan.splitlines() if "unfilled" not in line and "DAILY" not in line)
    print(f"[dry-run] filled={filled}/10 (2 market + quality) key_dups_dropped={key_dups}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run the 10-post daily quality schedule")
    parser.add_argument("--no-embed", action="store_true", help="skip qwen3-4B embed dedup")
    parser.add_argument("--no-rerank", action="store_true", help="skip deep 4B rerank")
    parser.add_argument("--posted", type=int, default=0, help="quality slots already used today")
    parser.add_argument("--live", action="store_true", help="include live RAG main drafts")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
