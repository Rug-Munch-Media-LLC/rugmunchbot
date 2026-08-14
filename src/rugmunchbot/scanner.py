#!/usr/bin/env python3
"""
RugMunchBot -- Scanning engines.

Token security scanner and wallet forensics analyzer.
Both functions fetch data from multiple sources and return structured results.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot.config import (
    DEXSCREENER,
    GOPLUS,
    HONEYPOT,
    SENTINEL_URL,
    backend_headers,
)
from rugmunchbot.formatters import detect_chain, is_evm, is_sol
from rugmunchbot.states import get_sol_price
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.registry import backend_json_get, backend_json_post

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# TODO(port): live config defines SOLANAFM_API / SENTINEL_TIMEOUT; the OSS
# config.py rewrite (checklist §3) does not carry them. Both are public
# no-auth constants — kept module-local so behavior is byte-identical. [REVIEW]
SOLANAFM_API = "https://api.solana.fm/v0"
SENTINEL_TIMEOUT = 60.0  # deep forensic scan can take a while (9+ modules)


# TODO(port): live calls app.rag.scan_memory.{schedule_scan_memory_index,
# should_index_scan} — private RAG product memory (checklist §2.2/§5). OSS
# ships fail-open no-ops so the scanner never blocks on an absent RAG backend;
# an HTTP adapter can replace them later. [REVIEW]
def should_index_scan(result: dict[str, Any], *, source: str = "scan_v1") -> bool:
    return False


def schedule_scan_memory_index(result: dict[str, Any], *, source: str = "scan_v1") -> None:
    return None


# TODO(port): live calls app.domains.scanner.gmgn_enrich.enrich_scan_with_gmgn
# (private GMGN holder/smart-money enrichment). OSS has no GMGN adapter yet —
# fail-open no-op so the scanner never blocks on the private backend. [REVIEW]
async def _enrich_scan_with_gmgn(
    scan_result: dict[str, Any], address: str, chain: str
) -> dict[str, Any]:
    return scan_result


# Consolidated risk-signal enrichment (bundle / deployer / wash / whale /
# authority / social cross-ref).
# TODO(port): live optionally imports app.domains.scanner.scan_enrich.enrich_scan
# (private risk-signal enrichment). OSS ships ``None`` (fail-open) until a
# public adapter exists — the guard below preserves the seam. [REVIEW]
enrich_scan: Callable[[dict[str, Any], str, str], Awaitable[dict[str, Any]]] | None = None

logger = get_logger("rmi_bot")


async def scan_token(address: str, chain: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "address": address,
        "chain": chain or detect_chain(address),
        "name": "Unknown",
        "symbol": "???",
        "price": 0,
        "mcap": 0,
        "fdv": 0,
        "volume_24h": 0,
        "liquidity": 0,
        "price_change_1h": 0,
        "price_change_24h": 0,
        "price_change_7d": 0,
        "holders": 0,
        "pair_address": "",
        "dex": "",
        "created_at": None,
        "risk_score": 0,
        "threats": [],
        "contract_verified": None,
        "honeypot": None,
        "buy_tax": None,
        "sell_tax": None,
        "lp_locked": None,
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "top_holders": [],
        "bundle_detected": False,
        "fresh_wallet_pct": 0,
        "exchange_pct": 0,
        "volume_real_ratio": None,
        "dev_wallet": None,
        "dev_other_tokens": [],
        "errors": [],
        "socials": {},
        "news_sentiment": None,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        # DexScreener
        try:
            r = await client.get(f"{DEXSCREENER}/tokens/{address}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    p = pairs[0]
                    result["name"] = p.get("baseToken", {}).get("name", "Unknown")
                    result["symbol"] = p.get("baseToken", {}).get("symbol", "???")
                    result["price"] = float(p.get("priceUsd", 0))
                    result["mcap"] = float(p.get("marketCap") or p.get("fdv") or 0)
                    result["fdv"] = float(p.get("fdv") or result["mcap"])
                    vol = p.get("volume", {})
                    result["volume_24h"] = float(vol.get("h24") or 0)
                    liq = p.get("liquidity", {})
                    result["liquidity"] = float(liq.get("usd") or 0)
                    pc = p.get("priceChange", {})
                    result["price_change_1h"] = pc.get("h1") or 0
                    result["price_change_24h"] = pc.get("h24") or 0
                    result["price_change_7d"] = pc.get("d7") or 0
                    result["pair_address"] = p.get("pairAddress", "")
                    result["dex"] = p.get("dexId", "")
                    result["chain"] = p.get("chainId", result["chain"])
                    result["created_at"] = p.get("pairCreatedAt")
                    socials = p.get("info", {}).get("socials", [])
                    result["socials"] = {s.get("type"): s.get("url") for s in socials}
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            httpx.HTTPError,
            httpx.TimeoutException,
        ) as e:
            result["errors"].append(f"DexScreener: {e}")

        # GoPlus Security (EVM)
        if is_evm(address):
            try:
                chain_id_map = {
                    "ethereum": "1",
                    "bsc": "56",
                    "polygon": "137",
                    "arbitrum": "42161",
                    "avalanche": "43114",
                    "base": "8453",
                    "optimism": "10",
                    "fantom": "250",
                }
                cid = chain_id_map.get(result["chain"].lower(), "1")
                r = await client.get(f"{GOPLUS}/token_security/{cid}?contract_addresses={address}")
                if r.status_code == 200:
                    sec = r.json().get("result", {}).get(address.lower(), {})
                    if sec:
                        result["contract_verified"] = sec.get("is_open_source") == "1"
                        result["honeypot"] = sec.get("is_honeypot") == "1"
                        result["buy_tax"] = float(sec.get("buy_tax") or 0)
                        result["sell_tax"] = float(sec.get("sell_tax") or 0)
                        result["mintable"] = sec.get("is_mintable") == "1"
                        result["can_blacklist"] = (
                            sec.get("is_blacklisted") == "1"
                            or sec.get("can_take_back_ownership") == "1"
                        )
                        result["owner_renounced"] = sec.get("is_owner_renounced") != "0"
                        lp_locks = sec.get("lp_holders", [])
                        locked_pct = sum(
                            float(h.get("percent", 0)) for h in lp_locks if h.get("is_locked") == 1
                        )
                        result["lp_locked"] = locked_pct
                        holders_data = sec.get("holders", [])
                        result["holders"] = len(holders_data) if holders_data else 0
                        for h in holders_data[:10]:
                            result["top_holders"].append(
                                {
                                    "address": h.get("address", ""),
                                    "pct": float(h.get("percent") or 0) * 100,
                                    "is_contract": h.get("is_contract") == 1,
                                    "tag": h.get("tag", ""),
                                }
                            )
                        if result["honeypot"]:
                            result["threats"].append("HONEYPOT DETECTED")
                            result["risk_score"] += 40
                        if not result["contract_verified"]:
                            result["threats"].append("Contract not verified")
                            result["risk_score"] += 15
                        if result["mintable"]:
                            result["threats"].append("Mintable supply")
                            result["risk_score"] += 20
                        if result["can_blacklist"]:
                            result["threats"].append("Owner can blacklist")
                            result["risk_score"] += 15
                        if not result["owner_renounced"]:
                            result["threats"].append("Ownership not renounced")
                            result["risk_score"] += 10
                        if result["buy_tax"] and result["buy_tax"] > 0.1:
                            result["threats"].append(
                                f"High buy tax: {result['buy_tax'] * 100:.1f}%"
                            )
                            result["risk_score"] += 10
                        if result["sell_tax"] and result["sell_tax"] > 0.1:
                            result["threats"].append(
                                f"High sell tax: {result['sell_tax'] * 100:.1f}%"
                            )
                            result["risk_score"] += 15
                        if locked_pct < 0.5 and result["mcap"] > 10000:
                            result["threats"].append(f"Low LP lock: {locked_pct * 100:.0f}%")
                            result["risk_score"] += 15
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                httpx.HTTPError,
                httpx.TimeoutException,
            ) as e:
                result["errors"].append(f"GoPlus: {e}")

        # Honeypot.is (EVM fallback)
        if is_evm(address) and result["honeypot"] is None:
            try:
                r = await client.get(f"{HONEYPOT}/IsHoneypot?address={address}")
                if r.status_code == 200:
                    hp = r.json()
                    result["honeypot"] = hp.get("isHoneypot", False)
                    if result["honeypot"]:
                        result["threats"].append("HONEYPOT (honeypot.is)")
                        result["risk_score"] += 40
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                httpx.HTTPError,
                httpx.TimeoutException,
            ) as e:
                result["errors"].append(f"Honeypot.is: {e}")

        # RMI Backend (own-data enrichment) — routed through the shared
        # backend_json_get helper so the auth header + budget gate live in one
        # place (rugmunchbot.tools.registry). None on failure = skip (fail-open).
        rmi = await backend_json_get(f"/api/v1/databus/token/{address}", timeout=10)
        if rmi:
            if rmi.get("bundle_detected"):
                result["bundle_detected"] = True
                result["threats"].append("Bundle activity detected")
                result["risk_score"] += 20
            if rmi.get("fresh_wallet_pct"):
                result["fresh_wallet_pct"] = rmi["fresh_wallet_pct"]
                if rmi["fresh_wallet_pct"] > 50:
                    result["threats"].append(
                        f"High fresh wallet ratio: {rmi['fresh_wallet_pct']:.0f}%"
                    )
                    result["risk_score"] += 10
            if rmi.get("dev_wallet"):
                result["dev_wallet"] = rmi["dev_wallet"]

    # Volume authenticity
    if result["volume_24h"] > 0 and result["mcap"] > 0:
        ratio = result["volume_24h"] / result["mcap"]
        result["volume_real_ratio"] = ratio
        if ratio > 5:
            result["threats"].append(f"Suspicious volume/mcap ratio: {ratio:.1f}x")
            result["risk_score"] += 15

    # Liquidity check
    if result["mcap"] > 0 and result["liquidity"] > 0:
        liq_ratio = result["liquidity"] / result["mcap"]
        if liq_ratio < 0.02:
            result["threats"].append("Very low liquidity vs mcap")
            result["risk_score"] += 20

    result["risk_score"] = min(result["risk_score"], 100)
    # GMGN holder/smart-money enrichment — best-effort + non-blocking
    # (hard 6s internal budget, fail-open; never raises). OSS: no-op adapter.
    result = await _enrich_scan_with_gmgn(result, address, result["chain"])
    # Risk-signal enrichment (bundle / deployer reputation / wash trading /
    # whale flow / authority / social cross-ref) — best-effort + non-blocking
    # (hard 8s budget, fail-open per module; never raises). OSS: no-op (None).
    if enrich_scan is not None:
        result = await enrich_scan(result, address, result["chain"])
    # Scan-to-RAG memory: auto-index the summary into the vector store so future
    # chat queries can recall "have we scanned this token before?" with risk
    # context. Background + fail-open — never blocks the response. OSS: no-op.
    if should_index_scan(result, source="scan_v1"):
        schedule_scan_memory_index(result, source="scan_v1")
    return result


async def scan_token_v2(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """v2 UnifiedTokenScanner — explainable multi-dimensional scoring.

    POSTs to /api/v2/scanner/token/scan and returns the raw v2 payload
    (safety_score, confidence, risk_level, risk_flags, green_flags,
    breakdown, evidence, modules_run). Returns None on any failure so
    callers can fall back to the legacy scanner.
    """
    data = await backend_json_post(
        "/api/v2/scanner/token/scan",
        json={"address": address, "chain": chain or detect_chain(address)},
        timeout=45,
    )
    if data is None:
        logger.warning("scan_token_v2 failed for %s", address)
        return None
    if data.get("status") != "ok":
        logger.warning("scan_token_v2 non-ok status for %s", address)
        return None
    # GMGN holder/smart-money enrichment — best-effort + non-blocking
    # (hard 6s internal budget, fail-open; never raises). OSS: no-op adapter.
    data = await _enrich_scan_with_gmgn(data, address, chain or detect_chain(address))
    # Risk-signal enrichment — best-effort + non-blocking, see scan_token.
    if enrich_scan is not None:
        data = await enrich_scan(data, address, chain or detect_chain(address))
    # Scan-to-RAG memory (background, fail-open) — see scan_token. OSS: no-op.
    if should_index_scan(data, source="scan_v2"):
        schedule_scan_memory_index(data, source="scan_v2")
    return data


async def sentinel_scan(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """SENTINEL deep forensic scan — POST the operator-supplied SENTINEL_URL.

    Runs the full 9-module SENTINEL pipeline (holder HHI, LP lock, dev
    reputation, contract authority, proxy, bundle, wash trading, honeypot,
    etc.) and returns the backend report dict::

        {
            "token_address", "chain", "scanned_at",
            "holder_analysis": {...},          # hhi, total_holders, warnings
            "liquidity_verification": {...},   # total_locked_percentage, status
            "dev_reputation": {...},           # reputation_score, rug_rate
            "contract_authority": {...},       # risk_level, warnings
            "composite_risk_score": 0-100,     # higher = riskier
            "risk_level": "unknown"|...,
            "red_flags": [...],
            "module_risk_scores": {...},
            "modules_run": [...],
            "errors": {...},
        }

    Returns None on any failure so callers can degrade gracefully (the
    sentinel deep scan is an enhancement over the base /scan, never a
    hard dependency).

    TODO(port): the live endpoint was the private payment-gate deep scan
    (scrub list #13). OSS uses the env-only ``SENTINEL_URL`` (operator's own
    deployment); unset = disabled. [REVIEW]
    """
    chain_norm = chain or detect_chain(address)
    if not SENTINEL_URL:
        logger.warning("sentinel_scan skipped — SENTINEL_URL not configured for %s", address)
        return None
    try:
        async with httpx.AsyncClient(timeout=SENTINEL_TIMEOUT) as client:
            r = await client.post(
                SENTINEL_URL,
                json={"address": address, "chain": chain_norm},
                headers=backend_headers(),
                timeout=SENTINEL_TIMEOUT,
            )
            if r.status_code != 200:
                logger.warning("sentinel_scan status %s for %s", r.status_code, address)
                return None
            data = r.json()
            if not isinstance(data, dict):
                logger.warning("sentinel_scan non-dict response for %s", address)
                return None
            data.setdefault("address", address)
            data.setdefault("chain", chain_norm)
            data["_source"] = "sentinel"
            # Scan-to-RAG memory (background, fail-open) — see scan_token.
            schedule_scan_memory_index(data, source="sentinel")
            return data
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("sentinel_scan failed for %s: %s", address, e)
        return None


async def fetch_hype_score(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """Fetch the C5 hype score from the backend DataBus ``hype_score`` provider.

    GETs /api/v1/databus/fetch/hype_score and returns the inner ``data``
    payload::

        {
            "hype_score": 0-100,
            "signals": {
                "social_velocity": {"score", "detail", ...},
                "volume_growth": {"score", "detail", ...},
                "holder_growth": {"score", "detail", ...},
                "price_momentum": {"score", "detail", ...},
            },
            "decay_factor": ...,
            "inactivity_hours": ...,
        }

    Returns None on any failure so callers can fall back to the local
    DexScreener-based hype computation.
    """
    params: dict[str, str] = {"address": address}
    if chain:
        params["chain"] = chain
    payload = await backend_json_get("/api/v1/databus/fetch/hype_score", params=params, timeout=20)
    if payload is None:
        logger.warning("fetch_hype_score failed for %s", address)
        return None
    data = payload.get("data") or payload
    if not isinstance(data, dict) or data.get("result") != "scanned":
        logger.warning("fetch_hype_score non-scanned result for %s", address)
        return None
    return data


async def fetch_deployer_fingerprint(
    deployer: str, chain: str | None = None
) -> dict[str, Any] | None:
    """Fetch the C7 deployer behavioral fingerprint from the backend DataBus.

    GETs /api/v1/databus/fetch/deployer_fingerprint and returns the inner
    ``data`` payload::

        {
            "deployer": "0x...",
            "risk_bucket": "NEW" | "REPEAT_CLEAN" | "REPEAT_RUG" | "FARM" | "PROLIFIC",
            "risk_score": 0-100,
            "total_deployments": int,
            "success_rate": float,
            "median_lifespan_hours": float,
            "funder_profile": {...},
            "launchpad": "pump.fun" | ...,   # only when factory-resolved
            "real_deployer": "0x...",        # only when factory-resolved
        }

    Returns None on any failure so callers can fall back to the token-scan
    deployer evidence.
    """
    params: dict[str, str] = {"deployer": deployer}
    if chain:
        params["chain"] = chain
    payload = await backend_json_get(
        "/api/v1/databus/fetch/deployer_fingerprint", params=params, timeout=20
    )
    if payload is None:
        logger.warning("fetch_deployer_fingerprint failed for %s", deployer)
        return None
    data = payload.get("data") or payload
    if not isinstance(data, dict) or data.get("result") != "scanned":
        logger.warning("fetch_deployer_fingerprint non-scanned result for %s", deployer)
        return None
    return data


async def analyze_wallet_v2(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """v2 UnifiedWalletScanner — entity clustering, scam exposure, smart money.

    POSTs to /api/v2/scanner/wallet/scan and returns the raw v2 payload
    (entity_name, entity_type, persona, scam_exposure_pct,
    smart_money_overlap, behavior_patterns, risk_forecast, risk_score,
    risk_flags). Returns None on failure so callers can fall back to the
    legacy wallet analyzer.
    """
    data = await backend_json_post(
        "/api/v2/scanner/wallet/scan",
        json={"address": address, "chain": chain or detect_chain(address)},
        timeout=45,
    )
    if data is None:
        logger.warning("analyze_wallet_v2 failed for %s", address)
        return None
    if data.get("status") != "ok":
        logger.warning("analyze_wallet_v2 non-ok status for %s", address)
        return None
    return data


def v2_token_to_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a /api/v2/scanner/token/scan response into the legacy
    report dict shape used by format_scan_report(), preserving the new
    explainable-scoring fields (safety_score, confidence, risk_level,
    green_flags, breakdown, evidence, modules_run)."""
    evidence = data.get("evidence") or {}
    holders = evidence.get("holders") or {}
    liquidity = evidence.get("liquidity") or {}
    security = evidence.get("security") or {}
    security.get("checks") or {}
    dev = evidence.get("deployer") or {}
    mkt = evidence.get("market") or {}
    funding = evidence.get("funding_analysis") or {}
    overlap = evidence.get("holder_overlap") or {}
    risks = list(data.get("risk_flags") or [])
    green = list(data.get("green_flags") or [])
    safety = float(data.get("safety_score") or 0)

    return {
        "address": data.get("token", ""),
        "chain": data.get("chain", "solana"),
        "name": data.get("name") or "Unknown",
        "symbol": data.get("symbol") or "???",
        "price": float(data.get("price_usd") or 0),
        "mcap": float(data.get("market_cap_usd") or 0),
        "fdv": float(data.get("market_cap_usd") or 0),
        "volume_24h": float(data.get("volume_24h_usd") or 0),
        "liquidity": float(data.get("liquidity_usd") or 0),
        "price_change_1h": 0,
        "price_change_24h": 0,
        "price_change_7d": 0,
        "holders": int(holders.get("holder_count") or 0),
        "pair_address": "",
        "dex": "",
        "created_at": None,
        "risk_score": round(100 - safety, 1),
        "threats": risks,
        "contract_verified": None,
        "honeypot": None,
        "buy_tax": None,
        "sell_tax": None,
        "lp_locked": float(liquidity.get("locked_pct") or 0) / 100
        if liquidity.get("locked_pct") is not None
        else None,
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "top_holders": [],
        "bundle_detected": "bundle" in " ".join(risks).lower(),
        "fresh_wallet_pct": 0,
        "exchange_pct": 0,
        "volume_real_ratio": float(mkt.get("fake_volume_pct") or 0)
        if mkt.get("fake_volume_pct") is not None
        else None,
        "dev_wallet": dev.get("deployer_address") or dev.get("address") or None,
        "dev_other_tokens": [],
        "errors": [],
        "socials": {},
        # v2 explainable scoring
        "safety_score": safety,
        "confidence": float(data.get("confidence") or 0),
        "risk_level": data.get("risk_level") or "unknown",
        "green_flags": green,
        "breakdown": data.get("breakdown"),
        "evidence": evidence,
        "modules_run": list(data.get("modules_run") or []),
        # v2 funding analysis (exchange-funded / fresh wallets / scam overlap)
        "exchange_funded_pct": float(funding.get("exchange_funded_pct") or 0),
        # live-verbatim duplicate key (last wins); rmi-backend suppresses via
        # per-file-ignore, OSS keeps the inline noqa equivalent.
        "fresh_wallet_pct": float(funding.get("fresh_wallet_pct") or 0),  # noqa: F601
        "holder_overlap_pct": float(overlap.get("max_overlap_pct") or 0),
        # v2 SENTINEL phase (free/premium module buckets) — surfaced as
        # "SENTINEL" flags on /scan (see formatters.format_sentinel_flags).
        "sentinel": data.get("sentinel"),
        # v2 news sentiment (RugBrain news_v2)
        "news_sentiment": data.get("news_sentiment"),
        # v2 own-data intel (bundle launch detection, wallet freshness
        # metrics, repeat-deployer track record) — surfaced as the
        # "Advanced Analysis" / banner blocks on /scan. The backend exposes
        # these at the top level of the v2 payload; degrade gracefully when
        # absent (legacy or older backend).
        "bundle_analysis": data.get("bundle_analysis"),
        "wallet_metrics": data.get("wallet_metrics"),
        "deployer_profile": (
            data.get("deployer_profile")
            or (data.get("deep_profile") or {}).get("deployer_profile")
            or (evidence.get("deployer") or {}).get("deployer_profile")
        ),
        # v2 GMGN holder/smart-money enrichment (attached by
        # scan_token_v2 via enrich_scan_with_gmgn) — surfaced as the
        # "Smart Money / Holders" section on /scan.
        "gmgn": data.get("gmgn"),
    }


def v2_wallet_to_legacy(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a /api/v2/scanner/wallet/scan response into the legacy
    report dict shape used by format_wallet_report(), preserving the new
    v2 intel fields (entity_name, entity_type, persona, scam exposure,
    smart money overlap, behavior patterns, risk forecast)."""
    return {
        "address": data.get("address", ""),
        "chain": data.get("chain", "solana"),
        "balance_usd": float(data.get("total_value_usd") or 0),
        "tx_count": int(data.get("token_count") or 0),
        "first_seen": None,
        "wallet_age_days": 0,
        "top_tokens": [],
        "is_fresh": False,
        "is_exchange": (data.get("entity_type") or "").lower() == "exchange",
        "is_contract": (data.get("entity_type") or "").lower() == "contract",
        "risk_score": float(data.get("risk_score") or 0),
        "flags": list(data.get("risk_flags") or []),
        "recent_txs": [],
        "funding_source": None,
        "connected_wallets": [],
        "pnl_estimate": None,
        "errors": [],
        # v2 wallet intel
        "labels": list(data.get("labels") or []),
        "entity_name": data.get("entity_name") or "",
        "entity_type": data.get("entity_type") or "",
        "persona": data.get("persona") or "",
        "persona_confidence": int(data.get("persona_confidence") or 0),
        "scam_tokens_held": int(data.get("scam_tokens_held") or 0),
        "scam_exposure_pct": float(data.get("scam_exposure_pct") or 0),
        "smart_money_overlap": list(data.get("smart_money_overlap") or []),
        "behavior_patterns": list(data.get("behavior_patterns") or []),
        "risk_forecast": data.get("risk_forecast") or "",
        "modules_run": list(data.get("modules_run") or []),
        # v2 funding analysis — the wallet's exposure to scams IS its
        # holder-overlap-with-known-scams metric. Exchange/fresh percentages
        # are carried through defensively when the backend provides them.
        "exchange_funded_pct": data.get("exchange_funded_pct"),
        "fresh_wallet_pct": data.get("fresh_wallet_pct"),
        "holder_overlap_pct": float(data.get("scam_exposure_pct") or 0),
    }


async def analyze_wallet(address: str, chain: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "address": address,
        "chain": chain or detect_chain(address),
        "balance_usd": 0,
        "tx_count": 0,
        "first_seen": None,
        "wallet_age_days": 0,
        "top_tokens": [],
        "is_fresh": False,
        "is_exchange": False,
        "is_contract": False,
        "risk_score": 0,
        "flags": [],
        "recent_txs": [],
        "funding_source": None,
        "connected_wallets": [],
        "pnl_estimate": None,
        "errors": [],
        # funding analysis metrics (populated by v2 scanner when available)
        "exchange_funded_pct": None,
        "fresh_wallet_pct": None,
        "holder_overlap_pct": None,
    }
    # Own-data wallet intel — routed through the shared backend_json_get
    # helper (auth header + budget gate in one place). None on failure =
    # fall through to the public Solana.fm fallback below (fail-open).
    data = await backend_json_get(f"/api/v1/databus/wallet/{address}/tokens", timeout=10)
    if data:
        for k in [
            "balance_usd",
            "tx_count",
            "first_seen",
            "is_exchange",
            "is_contract",
            "risk_score",
            "flags",
            "funding_source",
            "pnl_estimate",
        ]:
            if k in data:
                result[k] = data[k]
        result["top_tokens"] = data.get("top_tokens", [])[:10]
        result["recent_txs"] = data.get("recent_txs", [])[:5]
        result["connected_wallets"] = data.get("connected_wallets", [])[:5]
        if result["first_seen"]:
            try:
                first = datetime.fromisoformat(result["first_seen"].replace("Z", "+00:00"))
                result["wallet_age_days"] = (datetime.now(UTC) - first).days
                result["is_fresh"] = result["wallet_age_days"] < 7
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as e:
                logger.debug("analyze_wallet_error", exc_info=True)
                logger.warning(f"Failed to parse first_seen for {address}: {e}")
        return result
    logger.warning(f"analyze_wallet RMI backend failed for {address}")
    async with httpx.AsyncClient(timeout=15) as client:
        if is_sol(address):
            try:
                r = await client.get(f"{SOLANAFM_API}/accounts/{address}")
                if r.status_code == 200:
                    data = r.json()
                    sol_price = await get_sol_price()
                    result["balance_usd"] = float(data.get("balance", 0)) / 1e9 * sol_price
                    result["tx_count"] = data.get("transaction_count", 0)
            except (
                ConnectionError,
                OSError,
                TimeoutError,
                httpx.HTTPError,
                httpx.TimeoutException,
            ) as e:
                result["errors"].append(f"Solana.fm: {e}")
    return result
