#!/usr/bin/env python3
"""ApiPass credit meter — the ≤5 credits/hr budget ledger."""

from __future__ import annotations

import os
from typing import Any

from .._base import get_db
from ._helpers import now_iso


def record_apipass_usage(
    *,
    endpoint: str = "",
    model: str = "",
    credits: float = 0.5,
    task_id: str = "",
    status: str = "created",
) -> bool:
    """Meter one ApiPass task creation (the ≤5 credits/hr budget ledger)."""
    conn = get_db()
    try:
        conn.execute(
            """
            INSERT INTO kol_apipass_usage (ts, endpoint, model, credits, task_id, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                str(endpoint or ""),
                str(model or ""),
                float(credits),
                str(task_id or ""),
                str(status),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def apipass_usage_summary() -> dict[str, Any]:
    """Credit-burn dashboard: calls, credits used, remaining of the free 50.

    ``RMI_APIPASS_FREE_CREDITS`` (default 50) is the free allowance; each
    task creation meters ``RMI_APIPASS_CREDITS_PER_CALL`` (default 0.5).
    """
    free_credits = float(os.getenv("RMI_APIPASS_FREE_CREDITS", "50"))
    credits_per_call = float(os.getenv("RMI_APIPASS_CREDITS_PER_CALL", "0.5"))
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM kol_apipass_usage").fetchone()
        credits_row = conn.execute(
            "SELECT COALESCE(SUM(credits), 0.0) AS c FROM kol_apipass_usage WHERE status = 'created'"
        ).fetchone()
        today = now_iso()[:10]
        today_row = conn.execute(
            "SELECT COUNT(*) AS n FROM kol_apipass_usage WHERE ts LIKE ?", (today + "%",)
        ).fetchone()
        endpoints = conn.execute(
            "SELECT endpoint, COUNT(*) AS n, COALESCE(SUM(credits), 0.0) AS credits "
            "FROM kol_apipass_usage GROUP BY endpoint ORDER BY credits DESC"
        ).fetchall()
        credits_used = float(credits_row["c"] or 0.0) if credits_row else 0.0
        return {
            "total_calls": int(total["n"] or 0) if total else 0,
            "credits_used": round(credits_used, 2),
            "free_allowance": free_credits,
            "credits_remaining": round(max(0.0, free_credits - credits_used), 2),
            "calls_today": int(today_row["n"] or 0) if today_row else 0,
            "calls_per_hour_budget": int(os.getenv("RMI_APIPASS_MAX_CALLS_PER_HOUR", "5")),
            "credits_per_call": credits_per_call,
            "estimated_calls_remaining": int((free_credits - credits_used) / credits_per_call),
            "by_endpoint": [
                {
                    "endpoint": str(e["endpoint"]),
                    "calls": int(e["n"]),
                    "credits": round(float(e["credits"]), 2),
                }
                for e in endpoints
            ],
        }
    finally:
        conn.close()
