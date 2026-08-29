#!/usr/bin/env python3
"""Budget-bounded KOL registry verification + name-change detection.

The honest path to 100% correct identification: every seeded handle is
re-checked against the live X API on the ApiPass budget (≤6 task
creations/hr ≈ 3 credits/hr; polls are free). For each handle checked:

* ``user-tweets`` returns tweets → handle confirmed (mark verified,
  screen_name matches stored → no change; screen_name differs → record the
  name change via the identity machinery).
* ``user-tweets`` returns nothing / task fails → the handle is dead,
  renamed (name changes surface as a different screen_name next sweep), or
  the account is protected — left unverified, never dropped blindly.

Verification is round-robin + index-persisted so a scheduled run (e.g. the
bot's own tick) advances through the whole registry without re-checking
handles. Run manually or hook to a cron/systemd timer.

Usage:
    .venv/bin/python scripts/verify_kol_registry.py [count]
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

DEFAULT_BATCH = int(os.getenv("RMI_KOL_VERIFY_BATCH", "2"))


async def _verify_one(handle: str, batch_results: dict[str, int]) -> None:
    from rugmunchbot.channels.kol_scoreboard import detect_name_change
    from rugmunchbot.db import get_kol_identity, mark_kol_verified, resolve_handle
    from rugmunchbot.tools.apipass_x import fetch_user_tweets

    try:
        tweets = await fetch_user_tweets(handle, limit=3)
    except Exception as exc:  # fail-open by contract
        print(f"  [fail-open] @{handle}: {str(exc)[:80]}")
        batch_results["failed"] += 1
        return

    if not tweets:
        print(f"  [unverified] @{handle}: no tweets returned (dead/renamed/protected)")
        batch_results["empty"] += 1
        return

    payload_handle = str(tweets[0].get("handle") or "").strip().lstrip("@")
    identity = resolve_handle(handle)
    kol_id = str(identity.get("kol_id") or handle.lower()) if identity else handle.lower()

    if payload_handle and payload_handle.lower() != handle.lower():
        changed = detect_name_change(handle, payload_handle)
        print(f"  [name-change{' ✓' if changed else '?'}] @{handle} → @{payload_handle}")
        batch_results["name_changes"] += 1
    else:
        mark_kol_verified(kol_id)
        row = get_kol_identity(kol_id)
        verified = bool(row and row.get("verified"))
        print(f"  [verified ✓] @{handle} ({verified})")
        batch_results["verified"] += 1


async def main(batch: int) -> int:
    from rugmunchbot.db import list_kol_identities
    from rugmunchbot.kol_registry_seed import build_seed_rows
    from rugmunchbot.tools.apipass_x import task_creates_remaining

    if task_creates_remaining() <= 0:
        print("[SKIP] ApiPass hourly budget spent — retry next hour.")
        return 1

    rows = list_kol_identities(limit=10000)
    pool = [str(r["screen_name"]) for r in rows if r.get("screen_name")] or [
        r[0] for r in build_seed_rows()
    ]
    if not pool:
        print("[SKIP] Registry empty — run scripts/seed_kol_registry.py first.")
        return 1

    offset = int(os.getenv("RMI_KOL_VERIFY_INDEX", "0"))
    batch = max(1, min(int(batch), task_creates_remaining()))
    batch = max(1, min(batch, len(pool)))
    chosen = [(offset + i) % len(pool) for i in range(batch)]
    batch_results = {"verified": 0, "empty": 0, "failed": 0, "name_changes": 0}

    print(f"verifying {batch} of {len(pool)} handles (budget left: {task_creates_remaining()})")
    for idx in chosen:
        await _verify_one(pool[idx], batch_results)

    next_index = (offset + batch) % len(pool)
    print(f"next RMI_KOL_VERIFY_INDEX={next_index} (persist to resume round-robin)")
    print(f"batch results: {batch_results}")
    return 0


if __name__ == "__main__":
    count = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_BATCH)
    raise SystemExit(asyncio.run(main(count)))
