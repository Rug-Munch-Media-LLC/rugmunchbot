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


class DeepScanNotAvailableError(Exception):
    """SENTINEL deep scan is not implemented/enabled on the backend (HTTP 404)."""


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
    """SENTINEL deep forensic scan (premium — Scout+ or ⭐250).

    Raises DeepScanNotAvailableError when the backend does not expose the
    SENTINEL endpoint yet (HTTP 404), so callers can fall back to a friendly
    "coming soon" message instead of a generic failure.
    """
    payload = {"chain": chain or detect_chain(address), "address": address, "depth": depth}
    async with httpx.AsyncClient(timeout=SENTINEL_TIMEOUT) as client:
        r = await client.post(SENTINEL_URL, json=payload, headers=backend_headers())
        if r.status_code == 404:
            raise DeepScanNotAvailableError(
                "SENTINEL deep scan endpoint not available on the backend"
            )
        r.raise_for_status()
        body = r.json()
        if not isinstance(body, dict):
            raise ScanUnavailableError("SENTINEL returned a non-JSON response")
        body.setdefault("address", address)
        body.setdefault("chain", payload["chain"])
        body["_source"] = "sentinel"
        return body


# ── Deep Scan data aggregators (wire to real APIs) ──

DEEP_SCAN_TIMEOUT = 20.0


async def fetch_scoring_rate(client: httpx.AsyncClient, address: str, chain: str) -> dict | None:
    """Call POST /api/v1/scoring/rate — get comprehensive safety rating."""
    try:
        r = await client.post(
            f"{BACKEND_URL}/api/v1/scoring/rate",
            json={"chain": chain, "address": address},
            headers=backend_headers(),
            timeout=DEEP_SCAN_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("scoring_rate failed for %s: %s", address[:12], exc)
    return None


async def fetch_scoring_deployer(
    client: httpx.AsyncClient, address: str, chain: str
) -> dict | None:
    """Call GET /api/v1/scoring/deployer/{chain}/{address} — deployer profile."""
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/scoring/deployer/{chain}/{address}",
            headers=backend_headers(),
            timeout=DEEP_SCAN_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("scoring_deployer failed for %s: %s", address[:12], exc)
    return None


async def fetch_scoring_wallet(client: httpx.AsyncClient, address: str, chain: str) -> dict | None:
    """Call GET /api/v1/scoring/wallet/{chain}/{address} — wallet labels."""
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/scoring/wallet/{chain}/{address}",
            headers=backend_headers(),
            timeout=DEEP_SCAN_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("scoring_wallet failed for %s: %s", address[:12], exc)
    return None


async def fetch_token_holders(client: httpx.AsyncClient, address: str, chain: str) -> dict | None:
    """Call GET /api/v2/scanner/token/holders/{chain}/{address} — holder distribution."""
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v2/scanner/token/holders/{chain}/{address}",
            headers=backend_headers(),
            timeout=DEEP_SCAN_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("token_holders failed for %s: %s", address[:12], exc)
    return None


async def fetch_token_graph(client: httpx.AsyncClient, address: str, chain: str) -> dict | None:
    """Call RugMaps token-graph endpoint — wallet clusters.  Tries both POST and GET."""
    try:
        r = await client.post(
            f"{BACKEND_URL}/api/v1/rugmaps/token-graph",
            json={"chain": chain, "address": address},
            headers=backend_headers(),
            timeout=DEEP_SCAN_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, dict):
                return data
        if r.status_code == 405:
            r = await client.get(
                f"{BACKEND_URL}/api/v1/rugmaps/token-graph",
                params={"chain": chain, "address": address},
                headers=backend_headers(),
                timeout=DEEP_SCAN_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    return data
    except Exception as exc:
        logger.debug("token_graph failed for %s: %s", address[:12], exc)
    return None


async def run_deep_scan_aggregate(address: str, chain: str) -> dict:
    """Run all deep scan data sources in parallel and return aggregated result."""
    result: dict = {
        "address": address,
        "chain": chain,
        "scoring": None,
        "deployer": None,
        "wallet_labels": None,
        "holders": None,
        "clusters": None,
        "sentinel": None,
        "errors": [],
        "bundle_detected": False,
        "bundle_wallets": 0,
        "bundle_pct": 0.0,
        "cluster_count": 0,
        "sentinel_findings": [],
    }

    async with httpx.AsyncClient(timeout=DEEP_SCAN_TIMEOUT) as client:
        # Run all fetches in parallel
        scoring_t = fetch_scoring_rate(client, address, chain)
        deployer_t = fetch_scoring_deployer(client, address, chain)
        wallet_t = fetch_scoring_wallet(client, address, chain)
        holders_t = fetch_token_holders(client, address, chain)
        graph_t = fetch_token_graph(client, address, chain)

        scoring, deployer, wallet_labels, holders, clusters = await asyncio.gather(
            scoring_t, deployer_t, wallet_t, holders_t, graph_t
        )

        result["scoring"] = scoring
        result["deployer"] = deployer
        result["wallet_labels"] = wallet_labels
        result["holders"] = holders
        result["clusters"] = clusters

        # ── Try SENTINEL (best-effort; not critical) ──
        try:
            sentinel = await sentinel_scan(address, chain)
            result["sentinel"] = sentinel
            # Extract sentinel findings for the report
            findings = sentinel.get("findings") or sentinel.get("warnings") or []
            result["sentinel_findings"] = findings
            # Check for bundle info in sentinel free/premium buckets
            sentinel_data = sentinel.get("sentinel") or {}
            for bucket in ("premium", "free"):
                bucket_data = (
                    sentinel_data.get(bucket, {}) if isinstance(sentinel_data, dict) else {}
                )
                bundle_data = bucket_data.get("bundle_detection") or {}
                if bundle_data:
                    result["bundle_detected"] = bool(
                        bundle_data.get("bundles_detected") or bundle_data.get("detected")
                    )
                    result["bundle_wallets"] = int(
                        bundle_data.get("deployer_funded_wallets")
                        or bundle_data.get("wallet_count")
                        or 0
                    )
                    result["bundle_pct"] = float(
                        bundle_data.get("deployer_funded_pct") or bundle_data.get("supply_pct") or 0
                    )
                dev_data = bucket_data.get("dev_reputation") or {}
                if dev_data:
                    result["dev_reputation"] = dev_data
        except DeepScanNotAvailableError:
            logger.info("SENTINEL not available for %s — using scoring API only", address[:12])
        except Exception as exc:
            result["errors"].append(f"SENTINEL: {exc}")

        # ── Analyze holders for bundle signals ──
        if holders and not result["bundle_detected"]:
            holders_list = holders.get("holders") or holders.get("top_holders") or []
            if isinstance(holders_list, list) and len(holders_list) > 2:
                # Simple heuristic: if top 3 wallets hold > 50% and were funded in first block
                top3_pct = sum(
                    float(h.get("pct", h.get("percentage", h.get("share", 0))) or 0)
                    for h in holders_list[:3]
                )
                # Check for same-block buys
                first_block = holders.get("first_block") or holders.get("creation_block")
                same_block_count = 0
                if first_block:
                    for h in holders_list:
                        h_block = h.get("first_tx_block") or h.get("block")
                        if h_block and h_block == first_block:
                            same_block_count += 1
                if same_block_count > 5:
                    result["bundle_detected"] = True
                    result["bundle_wallets"] = same_block_count
                    result["bundle_pct"] = min(100.0, top3_pct)
                elif top3_pct > 60:
                    result["bundle_detected"] = True
                    result["bundle_wallets"] = 3
                    result["bundle_pct"] = top3_pct

        # ── Analyze clusters from token graph ──
        if clusters:
            cluster_list = clusters.get("clusters") or clusters.get("wallet_clusters") or []
            if isinstance(cluster_list, list):
                result["cluster_count"] = len(cluster_list)
                # Flag suspicious clusters (linked to deployer)
                suspicious = [
                    c
                    for c in cluster_list
                    if c.get("suspicious") or c.get("connected") or c.get("deployer_linked")
                ]
                result["suspicious_clusters"] = len(suspicious)
                result["cluster_details"] = cluster_list[:5]

        # ── Compute composite risk from scoring API ──
        if scoring:
            result["risk_score"] = scoring.get("risk_score") or scoring.get("score") or 0
            result["grade"] = scoring.get("grade") or ""
            result["categories"] = scoring.get("categories") or scoring.get("breakdown") or {}
            result["flags"] = scoring.get("flags") or scoring.get("risk_flags") or []

    return result
