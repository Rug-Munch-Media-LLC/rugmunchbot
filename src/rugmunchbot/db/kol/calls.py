#!/usr/bin/env python3
"""``kol_calls`` rows — one per captured KOL call (handle, tweet, token)."""

from __future__ import annotations

import json
from typing import Any

from .._base import get_db
from ._helpers import norm_handle, now_iso


def record_kol_call(
    *,
    handle: str,
    tweet_id: str,
    token_symbol: str = "",
    token_address: str = "",
    chain: str = "",
    call_type: str = "buy",
    tweet_text: str = "",
    posted_at: str = "",
    source_url: str = "",
    wallet_address: str = "",
) -> bool:
    """Insert one KOL call; upserts by ``tweet_id`` (re-sweeps don't dup)."""
    handle = norm_handle(handle)
    tweet_id = str(tweet_id or "").strip()
    if not handle or not tweet_id:
        return False
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_calls (
                handle, tweet_id, token_symbol, token_address, chain, call_type,
                tweet_text, posted_at, source_url, wallet_address, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tweet_id) DO UPDATE SET
                handle = excluded.handle,
                token_symbol = COALESCE(NULLIF(excluded.token_symbol, ''), kol_calls.token_symbol),
                token_address = COALESCE(NULLIF(excluded.token_address, ''), kol_calls.token_address),
                chain = COALESCE(NULLIF(excluded.chain, ''), kol_calls.chain),
                call_type = excluded.call_type,
                tweet_text = COALESCE(NULLIF(excluded.tweet_text, ''), kol_calls.tweet_text),
                source_url = COALESCE(NULLIF(excluded.source_url, ''), kol_calls.source_url),
                wallet_address = COALESCE(NULLIF(excluded.wallet_address, ''), kol_calls.wallet_address)
            """,
            (
                handle,
                tweet_id,
                token_symbol,
                token_address,
                chain,
                call_type,
                tweet_text,
                posted_at,
                source_url,
                wallet_address,
                now_iso(),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def mark_kol_call_deleted(tweet_id: str) -> bool:
    """Mark one call as deleted (retracted) with the current timestamp."""
    tweet_id = str(tweet_id or "").strip()
    if not tweet_id:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE kol_calls SET is_deleted = 1, deleted_at = ? WHERE tweet_id = ?",
            (now_iso(), tweet_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_kol_call(tweet_id: str) -> dict[str, Any] | None:
    """One call row by tweet id (None when unknown)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM kol_calls WHERE tweet_id = ?", (str(tweet_id or ""),)
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_kol_calls(handle: str = "", limit: int = 200) -> list[dict[str, Any]]:
    """Recent calls, optionally scoped to one handle (newest captured first)."""
    conn = get_db()
    try:
        if handle:
            rows = conn.execute(
                "SELECT * FROM kol_calls WHERE handle = ? ORDER BY captured_at DESC LIMIT ?",
                (norm_handle(handle), max(1, limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM kol_calls ORDER BY captured_at DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_kol_call_result(tweet_id: str, result: str) -> bool:
    """Best-effort outcome for one call: win | loss | flat | unknown."""
    result = str(result or "").strip().lower()
    if result not in {"win", "loss", "flat", "unknown"}:
        return False
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE kol_calls SET result = ? WHERE tweet_id = ? AND result = 'unknown'",
            (result, str(tweet_id or "").strip()),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def export_kol_calls_jsonl(path: str, limit: int = 500) -> int:
    """Write recent calls as JSON Lines in the backend ``CallStore`` shape.

    The rmi-backend site router (``GET /api/v1/kol/scammer-calls``) reads
    exactly this schema from ``app/databus/scammer_calls.json``; an operator
    can point that store at this export (or copy it) so the KOL hall of
    shame renders on the site. Returns rows written (0 on failure).
    """
    try:
        calls = list_kol_calls(limit=limit)
        with open(path, "w", encoding="utf-8") as fh:
            for c in calls:
                record = {
                    "id": f"kol-call-{c.get('tweet_id') or c.get('id')}",
                    "handle": c.get("handle"),
                    "channel": "x",
                    "post_text": (c.get("tweet_text") or "")[:500],
                    "token_symbol": c.get("token_symbol") or None,
                    "token_address": c.get("token_address") or None,
                    "chain": c.get("chain") or None,
                    "severity": "critical" if (c.get("is_deleted") or 0) else "medium",
                    "captured_at": c.get("captured_at"),
                    "source_url": c.get("source_url") or None,
                }
                fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        return len(calls)
    except OSError:
        return 0
