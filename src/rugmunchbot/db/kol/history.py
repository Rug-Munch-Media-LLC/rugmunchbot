#!/usr/bin/env python3
"""Documented rug/dump history seeding (loss calls from evidence URLs)."""

from __future__ import annotations

import hashlib

from .calls import record_kol_call, update_kol_call_result


def seed_kol_rug_history(rows: list[dict[str, str]]) -> int:
    """Seed documented call outcomes (rug/dump history) as loss calls.

    Each row: ``{handle, token_symbol, chain, date, outcome, evidence_url,
    notes}``. The tweet_id is a deterministic storage key derived from the
    evidence URL (never a fabricated tweet); result is always ``loss`` for
    the fleet's documented dumped/rugged outcomes. Returns rows recorded.
    """
    count = 0
    for r in rows:
        handle = str(r.get("handle") or "").strip().lstrip("@")
        token = str(r.get("token_symbol") or "").strip()
        url = str(r.get("evidence_url") or "").strip()
        date = str(r.get("date") or "").strip()
        if not handle or not token:
            continue
        key_src = f"{handle}:{token}:{date}:{url or ''}"
        tweet_id = "rug-" + hashlib.sha256(key_src.encode()).hexdigest()[:16]
        text = f"Called ${token} ({date}) — documented loss outcome."
        if url:
            text += f" Evidence: {url}"
        recorded = record_kol_call(
            handle=handle,
            tweet_id=tweet_id,
            token_symbol=token,
            chain=str(r.get("chain") or ""),
            call_type="buy",
            tweet_text=text[:500],
            posted_at=date,
            source_url=url,
        )
        if recorded:
            update_kol_call_result(tweet_id, "loss")
            count += 1
    return count
