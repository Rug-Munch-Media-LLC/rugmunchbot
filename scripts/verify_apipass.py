#!/usr/bin/env python3
"""Verify the ApiPass X source end-to-end with a real tweet pull.

Usage:
    # 1. Store the key (never in git):
    #    gopass insert rmi/apipass/api_key        # or export APIPASS_API_KEY=apk_...
    # 2. Run:
    #    .venv/bin/python scripts/verify_apipass.py [tweet_id]

Exit codes: 0 = real tweet pulled and printed; 1 = fail-open (no key /
rate-limited / API error) — run with the key configured for the real pull.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# A well-known stable crypto tweet id to prove the pull (a real tweet id).
DEFAULT_TWEET_ID = os.getenv("APIPASS_VERIFY_TWEET_ID", "1")


async def main() -> int:
    from rugmunchbot.db import apipass_usage_summary
    from rugmunchbot.tools.apipass_x import (
        api_key_configured,
        fetch_tweet_detail,
        task_creates_remaining,
    )

    if not api_key_configured():
        print("[SKIP] No ApiPass key configured.")
        print(
            "        Store it in the deployment secrets dir (apipass_api_key), export APIPASS_API_KEY,"
        )
        print("        or gopass insert rmi/apipass/api_key")
        print("        Then re-run this script for the real tweet pull.")
        return 1

    print(f"ApiPass budget remaining this hour: {task_creates_remaining()}")
    usage = apipass_usage_summary()
    print(
        f"ApiPass ledger: {usage['credits_used']:.2f}/{usage['free_allowance']:.2f} "
        f"credits used ({usage['total_calls']} calls total, {usage['calls_today']} today) — "
        f"~{usage['estimated_calls_remaining']} calls left of the free 50"
    )
    tweet_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TWEET_ID
    tweet = await fetch_tweet_detail(tweet_id)
    if not tweet:
        print(f"[FAIL-OPEN] No tweet returned for id {tweet_id} (task failed or 404).")
        return 1
    print("=== REAL TWEET PULLED ===")
    print(f"id:      {tweet.get('tweet_id')}")
    print(f"author:  @{tweet.get('handle')} ({tweet.get('author_name')})")
    print(f"created: {tweet.get('created_at')}")
    print(f"likes:   {tweet.get('like_count')}  retweets: {tweet.get('retweet_count')}")
    print(f"url:     {tweet.get('url')}")
    print(f"text:    {tweet.get('text')[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
