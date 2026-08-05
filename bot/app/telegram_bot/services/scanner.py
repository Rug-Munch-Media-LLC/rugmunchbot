#!/usr/bin/env python3
"""
V3 Scanner API clients — RugMunchScan v2 (with v1 async+poll fallback)
and SENTINEL deep forensic scans.

Strategy (per TELEGRAM-BOT-V3-DESIGN.md §4.2): try the synchronous v2
contract first; if it is unavailable, fall back to the v1 async scan and
poll until completion. The legacy DataBus path stays in bot.py and is
injected by the bot at startup for when both v2 and v1 fail.
"""

import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from config import (
    BACKEND_URL,
    DATABUS_URL,
    RMI_AUTH_TOKEN,
    SCANNER_V1_URL,
    SCANNER_V2_URL,
    SENTINEL_URL,
)

logger = logging.getLogger(__name__)

SCAN_TIMEOUT = 30.0
SENTINEL_TIMEOUT = 60.0
V1_POLL_TRIES = 8
V1_POLL_INTERVAL = 2.0

_EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class ScanUnavailableError(Exception):
    """Scanner backend unreachable after v2 and v1 fallback attempts."""


def backend_headers() -> dict[str, str]:
    """Auth headers for backend calls (X-API-Key from env, never hardcoded)."""
    headers = {"Content-Type": "application/json"}
    if RMI_AUTH_TOKEN:
        headers["X-API-Key"] = RMI_AUTH_TOKEN
    return headers


def detect_chain(address: str) -> str:
    """Best-effort chain detection: 0x-prefixed hex -> ethereum, base58 -> solana."""
    if _EVM_RE.match(address):
        return "ethereum"
    if _SOL_RE.match(address):
        return "solana"
    return "unknown"


async def _enrich_display(client: httpx.AsyncClient, data: dict, address: str) -> dict:
    """Fill display fields (name/symbol/price/mcap) from DataBus when the v2
    contract does not include them. Never raises — enrichment is best-effort."""
    if data.get("name") and data.get("symbol"):
        return data
    try:
        r = await client.get(
            f"{DATABUS_URL}/fetch/token_price",
            params={"mint": address},
            timeout=5,
        )
        if r.status_code == 200:
            payload = r.json().get("data", r.json())
            pairs = payload.get("pairs", []) if isinstance(payload, dict) else []
            if pairs:
                p = pairs[0]
                data.setdefault("name", p.get("baseToken", {}).get("name", "Unknown"))
                data.setdefault("symbol", p.get("baseToken", {}).get("symbol", "???"))
                market = data.setdefault("market", {})
                price = float(p.get("priceUsd", 0) or 0)
                if price and not market.get("price_usd"):
                    market["price_usd"] = price
                mcap = float(p.get("marketCap") or p.get("fdv") or 0)
                if mcap and not market.get("mcap_usd"):
                    market["mcap_usd"] = mcap
                liq = p.get("liquidity", {})
                liq_usd = float(liq.get("usd") or 0)
                if liq_usd and not data.get("liquidity", {}).get("usd"):
                    data.setdefault("liquidity", {})["usd"] = liq_usd
                data.setdefault("chain", p.get("chainId", data.get("chain", "unknown")))
    except httpx.HTTPError as exc:
        logger.debug("enrichment skipped for %s: %s", address, exc)
    return data


async def scan_token_v2(address: str, chain: str | None = None, depth: str = "quick") -> dict:
    """RugMunchScan v2 synchronous; fall back to v1 async + poll."""
    payload = {"chain": chain or detect_chain(address), "address": address, "depth": depth}
    async with httpx.AsyncClient(timeout=SCAN_TIMEOUT) as client:
        # 1) v2 synchronous
        try:
            r = await client.post(SCANNER_V2_URL, json=payload, headers=backend_headers())
            if r.status_code == 200:
                body = r.json()
                if isinstance(body, dict):
                    body.setdefault("address", address)
                    body.setdefault("chain", payload["chain"])
                    body["_source"] = "v2"
                    return await _enrich_display(client, body, address)
            logger.warning("scan v2 returned %s; falling back to v1", r.status_code)
        except httpx.HTTPError as exc:
            logger.debug("scan v2 failed (%s); falling back to v1", exc)

        # 2) v1 async + poll
        try:
            r = await client.post(SCANNER_V1_URL, json=payload, headers=backend_headers())
            r.raise_for_status()
            body = r.json()
            scan_id = body.get("scan_id") if isinstance(body, dict) else None
            if not scan_id:
                raise ScanUnavailableError("v1 scan returned no scan_id")
            for _ in range(V1_POLL_TRIES):
                await asyncio.sleep(V1_POLL_INTERVAL)
                poll = await client.get(
                    f"{BACKEND_URL}/api/v1/scanner/scan/{scan_id}",
                    headers=backend_headers(),
                )
                if poll.status_code != 200:
                    continue
                result = poll.json()
                if not isinstance(result, dict):
                    continue
                status = result.get("status")
                if status in ("completed", "done", "success"):
                    result.setdefault("address", address)
                    result.setdefault("chain", payload["chain"])
                    result["_source"] = "v1"
                    return result
                if status in ("failed", "error"):
                    raise ScanUnavailableError(f"v1 scan failed: {result.get('error', 'unknown')}")
            raise ScanUnavailableError("v1 scan timed out while polling")
        except httpx.HTTPError as exc:
            raise ScanUnavailableError(f"v1 scan unavailable: {exc}") from exc


async def sentinel_scan(address: str, chain: str | None = None, depth: str = "deep") -> dict:
    """SENTINEL deep forensic scan (premium — Scout+ or ⭐250)."""
    payload = {"chain": chain or detect_chain(address), "address": address, "depth": depth}
    async with httpx.AsyncClient(timeout=SENTINEL_TIMEOUT) as client:
        r = await client.post(SENTINEL_URL, json=payload, headers=backend_headers())
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict):
            raise ScanUnavailableError("SENTINEL returned a non-JSON response")
        body.setdefault("address", address)
        body.setdefault("chain", payload["chain"])
        body["_source"] = "sentinel"
        return body
