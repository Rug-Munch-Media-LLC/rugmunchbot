"""LP-expiry monitor — alert before liquidity unlocks (win 35).

Scans tokens with LP locks approaching expiry. A locked-LP token whose
lock expires in <7 days is a time bomb: the deployer can pull liquidity
at will. This gives holders advance warning to exit before the pull.

Data: backend lp-health endpoint (DataBus-backed). Tracks tokens from
recent scans + alerts; dedupes so the same expiry never double-alerts.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from rugmunchbot.config._endpoints import backend_headers

logger = logging.getLogger(__name__)

BACKEND = os.getenv("BACKEND_URL", "http://localhost:8000")
EXPIRY_WINDOW_DAYS = float(os.getenv("LP_EXPIRY_WINDOW_DAYS", "7"))
_DEDUPE_TTL = float(os.getenv("LP_EXPIRY_DEDUPE_TTL", "86400"))  # 24h
_seen: dict[str, float] = {}


def _f(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


async def check_lp_expiry(address: str, chain: str = "eth") -> dict[str, Any]:
    """Check one token's LP lock status and return expiry analysis."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{BACKEND}/api/v1/lp-health/check/{address}",
                params={"chain": chain},
                headers=backend_headers(),
            )
            if r.status_code != 200:
                return {"error": f"lp-health HTTP {r.status_code}"}
            health = r.json()
    except Exception as exc:
        return {"error": str(exc)[:140]}

    locked_pct = _f(health.get("lp_locked_pct"))
    unlock_ts = _f(health.get("lp_unlock_timestamp"))
    lock_days_remaining = _f(health.get("lock_days_remaining"))

    if locked_pct is None or locked_pct <= 0:
        return {"address": address, "status": "no_lock", "risk": "LP not locked — can be pulled anytime"}

    if unlock_ts is None and lock_days_remaining is None:
        return {"address": address, "status": "locked_no_expiry", "locked_pct": locked_pct,
                "risk": "locked but no expiry data available"}

    if lock_days_remaining is None and unlock_ts is not None:
        lock_days_remaining = (unlock_ts - time.time()) / 86400

    days = lock_days_remaining or 0
    if days > EXPIRY_WINDOW_DAYS:
        return {"address": address, "status": "safe", "days_remaining": round(days, 1),
                "locked_pct": locked_pct}

    severity = "CRITICAL" if days <= 2 else "WARNING"
    return {
        "address": address,
        "chain": chain,
        "status": "EXPIRING",
        "severity": severity,
        "days_remaining": round(days, 1),
        "locked_pct": locked_pct,
        "risk": (
            f"LP lock expires in {days:.1f} days ({locked_pct:.0f}% locked) — "
            f"deployer can pull liquidity. Exit window closing."
        ),
    }


async def scan_lp_expiries(tokens: list[dict[str, str]]) -> dict[str, Any]:
    """Scan multiple tokens for LP expiry risk. Returns expiring only."""
    expiring: list[dict[str, Any]] = []
    now = time.time()
    for tok in tokens[:20]:  # cap batch size
        addr = tok.get("address") or tok.get("token_address") or ""
        chain = tok.get("chain") or "eth"
        if not addr:
            continue
        result = await check_lp_expiry(addr, chain)
        if result.get("status") == "EXPIRING":
            dedupe_key = f"{addr}:{result.get('days_remaining', 0):.0f}"
            if dedupe_key in _seen and now - _seen[dedupe_key] < _DEDUPE_TTL:
                continue
            _seen[dedupe_key] = now
            result["symbol"] = tok.get("symbol", "")
            expiring.append(result)
    return {"expiring": expiring, "scanned": len(tokens), "flagged": len(expiring)}
