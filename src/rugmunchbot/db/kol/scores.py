#!/usr/bin/env python3
"""``kol_scores`` — cached per-handle report-card aggregates."""

from __future__ import annotations

from typing import Any

from .._base import get_db
from ._helpers import norm_handle, now_iso


def upsert_kol_score(
    *,
    handle: str,
    total_calls: int,
    deleted_calls: int,
    shill_calls: int,
    scam_token_calls: int,
    tokens_mentioned: int,
    score: float,
    grade: str,
) -> None:
    """Cache one handle's aggregated report card."""
    handle = norm_handle(handle)
    if not handle:
        return
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_scores (
                handle, total_calls, deleted_calls, shill_calls, scam_token_calls,
                tokens_mentioned, score, grade, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(handle) DO UPDATE SET
                total_calls = excluded.total_calls,
                deleted_calls = excluded.deleted_calls,
                shill_calls = excluded.shill_calls,
                scam_token_calls = excluded.scam_token_calls,
                tokens_mentioned = excluded.tokens_mentioned,
                score = excluded.score,
                grade = excluded.grade,
                updated_at = excluded.updated_at
            """,
            (
                handle,
                int(total_calls),
                int(deleted_calls),
                int(shill_calls),
                int(scam_token_calls),
                int(tokens_mentioned),
                float(score),
                str(grade or "N/A"),
                now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_kol_scores(limit: int = 100) -> list[dict[str, Any]]:
    """Cached report cards, worst first (highest shame score on top)."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM kol_scores ORDER BY score DESC, deleted_calls DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
