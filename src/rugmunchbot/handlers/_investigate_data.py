"""Investigation data + verdict helpers (split from handlers/investigate.py, 2026-09-18)."""

from __future__ import annotations

from typing import Any

import httpx

from rugmunchbot.config import BACKEND_URL, backend_headers
from rugmunchbot.scanner import scan_token, scan_token_v2, v2_token_to_legacy
from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


def _determine_verdict(scan_result: dict[str, Any], wallet_data: dict[str, Any]) -> tuple[str, str]:
    """Determine overall investigation verdict."""
    risk = scan_result.get("risk_score") or 0
    threats = len(scan_result.get("threats") or [])
    wallet_flags = len((wallet_data or {}).get("flags") or [])
    bundle = bool(scan_result.get("bundle_detected"))
    fresh = (scan_result.get("fresh_wallet_pct") or 0) > 50
    honeypot = bool(scan_result.get("honeypot"))
    low_liq = (scan_result.get("liquidity") or 0) < 1000 and (scan_result.get("mcap") or 0) > 10000

    score = risk
    if honeypot:
        score = 100
    if bundle:
        score += 15
    if fresh:
        score += 10
    if low_liq:
        score += 10
    if wallet_flags > 2:
        score += 10

    score = min(score, 100)

    if score >= 80:
        return "CRITICAL", f"DANGER: {threats} threats, {wallet_flags} wallet flags"
    elif score >= 60:
        return "HIGH", f"RISKY: {threats} threats found"
    elif score >= 40:
        return "MEDIUM", f"CAUTION: {threats} issues detected"
    elif score >= 20:
        return "LOW", "LOW RISK: Minor concerns"
    else:
        return "SAFE", "Clean — no significant risks detected"


async def _run_scan(address: str, chain: str) -> dict[str, Any]:
    """Security scan — v2 explainable first, legacy fallback."""
    scan_v2 = await scan_token_v2(address, chain)
    if scan_v2 is not None:
        scan_result = v2_token_to_legacy(scan_v2)
        if chain:
            scan_result["chain"] = chain
        return scan_result
    return await scan_token(address, chain)


async def _fetch_wallet_tokens(client: httpx.AsyncClient, address: str) -> dict[str, Any]:
    """Fetch wallet token holdings from the backend (best-effort)."""
    try:
        wallet_resp = await client.get(
            f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens",
            headers=backend_headers(),
            timeout=10,
        )
        if wallet_resp.status_code == 200:
            return wallet_resp.json()
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.warning("investigate backend fetch failed: %s", e)
    return {}


async def _fetch_cross_chain(client: httpx.AsyncClient, address: str, chain: str) -> list[Any]:
    """Fetch cross-chain profile from the backend (best-effort)."""
    try:
        cc_resp = await client.get(
            f"{BACKEND_URL}/api/v1/cross-chain/{address}",
            params={"chain": chain},
            headers=backend_headers(),
            timeout=10,
        )
        if cc_resp.status_code == 200:
            profile = cc_resp.json()
            primary = profile.get("primary_chain", chain)
            return [
                {
                    "source_chain": primary,
                    "dest_chain": m.get("chain", ""),
                    "address": m.get("address", ""),
                    "confidence": m.get("confidence", 0),
                }
                for m in profile.get("addresses", [])
            ]
    except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
        logger.warning("investigate history fetch failed: %s", e)
    return []
