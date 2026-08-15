"""Canonical scanner engine for RugMunchBot — /scan + chat scanner tool.

UNIFIED SCANNER ENGINE (audit 2026-08-15): this module is the single source
of truth for token/wallet scanning. The old ``rugmunchbot/scanner.py`` used
to own its own ``POST /api/v2/scanner/token/scan`` client + payload shape
while ``scanner_intel`` (chat tool) duplicated the same POST with a second
client — two sources of truth, behavior drift. Now:

  * ``scan_token_v2`` — the ONE v2 scan POST, via the tools' shared
    ``backend_json_post`` client (pooled, budget-gated, same auth header
    source as every other tool).
  * ``scanner_intel`` (chat tool) calls ``scan_token_v2`` — same payload
    shape, one engine.
  * ``scanner.py`` (legacy module) is a thin re-export shim over this
    module, so ``handlers/_scan_handlers.py`` / ``investigate.py`` /
    ``payments.py`` keep their public names with zero call-site rewrites.

The engine also owns the fallback path (fast bot scan, DexScreener direct),
the legacy multi-source scan (DexScreener + GoPlus + Honeypot.is + RMI
databus), wallet analysis, SENTINEL deep scan, hype/deployer intel, and the
v2→legacy payload converters.

Call order (own data first, automatic fallback when our backend is down or
rate-limited — see module docstring of ``tools/token_meta.py`` for the
identity/image resolution order):

  1. Own backend unified v2 scan (``POST /api/v2/scanner/token/scan``) — the
     50+ module SENTINEL-unified scan: safety_score, confidence, risk_level,
     risk_flags, green_flags, evidence (security checks incl. honeypot),
     bundle_analysis (bundle detection), wallet_metrics + funding_analysis
     (holder analysis), liquidity_verification, deployer profile, news.
  2. Own backend fast bot scan (``POST /api/v1/bot/scan``) — Redis-cached
     quick scan (risk_score, is_honeypot, is_mintable, holders, liquidity).
  3. DexScreener direct (``GET /latest/dex/tokens/{address}``) — price /
     liquidity / volume only; risk fields are marked unavailable.

Every result embeds the canonical ``token`` identity block (logo + name +
symbol + chain + price) from ``resolve_token_meta``, so a scanner answer can
ALWAYS render the token image — "no image available" when no source has one.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from rugmunchbot.config import (
    BACKEND_URL,
    DEXSCREENER,
    GOPLUS,
    HONEYPOT,
    backend_headers,
)
from rugmunchbot.formatters import detect_chain, is_evm, is_sol
from rugmunchbot.states import get_sol_price
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import get_shared_client, http_get_json
from rugmunchbot.tools.registry import ToolResult, backend_json_post
from rugmunchbot.tools.token_meta import token_meta

# Public no-auth constants (not carried by the OSS config rewrite — checklist
# §3). Kept module-local so behavior is byte-identical.
SOLANAFM_API = "https://api.solana.fm/v0"
SENTINEL_TIMEOUT = 60.0  # deep forensic scan can take a while (9+ modules)
SENTINEL_URL = os.getenv("SENTINEL_URL", f"{BACKEND_URL}/api/v1/x402-tools/sentinel_scan")


# OSS fail-open adapter for the private ``app.domains.scanner.gmgn_enrich``
# (GMGN holder/smart-money enrichment). No public GMGN adapter ships yet —
# pass-through so the scanner never blocks on the private backend.
async def enrich_scan_with_gmgn(
    scan_result: dict[str, Any], address: str, chain: str
) -> dict[str, Any]:
    return scan_result


# OSS fail-open no-ops for the private ``app.rag.scan_memory`` RAG product
# memory hooks — scan results are never blocked on an absent RAG backend;
# an HTTP adapter can replace them later.
def should_index_scan(result: dict[str, Any], *, source: str = "scan_v1") -> bool:
    return False


def schedule_scan_memory_index(result: dict[str, Any], *, source: str = "scan_v1") -> None:
    return None


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Consolidated risk-signal enrichment (bundle / deployer / wash / whale /
# authority / social cross-ref). Private ``app.domains.scanner.scan_enrich``
# stays in rmi-backend — OSS ships ``None`` (fail-open) until a public
# adapter exists; the guard below preserves the seam.
enrich_scan: Callable[[dict[str, Any], str, str], Awaitable[dict[str, Any]]] | None = None

logger = get_logger("rmi_bot.tools.scanner")


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce a value to float; null/unparseable values never crash a scan.

    DexScreener / GoPlus / Solana.fm payloads carry nulls (``priceUsd: null``
    on delisted pairs) and occasionally garbage strings; ``float(None)``
    raises ``TypeError`` and would kill the whole scan reply. Guarded exactly
    as the audit asks: ``try/except (TypeError, ValueError): 0.0``.
    """
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


async def _token_meta_dict(address: str, chain: str, symbol: str = "") -> dict[str, Any]:
    """Resolve the token identity block through the cached ``token_meta`` tool.

    ``scanner_intel`` previously called ``resolve_token_meta`` directly,
    bypassing the token_meta cache — every scan re-resolved identity over the
    network. Routing through the public tool reuses the Redis identity/price
    cache (600s / 60s) so repeated scans of the same token are cheap.
    """
    tr = await token_meta(address=address, chain=chain, symbol=symbol)
    return tr.data if isinstance(tr.data, dict) else {}


def _detect_chain(address: str) -> str:
    """0x prefix → ethereum, otherwise solana (mirrors backend auto-detect)."""
    addr = (address or "").strip()
    if addr.startswith(("0x", "0X")):
        return "ethereum"
    return "solana"


def _security_checks(evidence: dict[str, Any]) -> dict[str, Any]:
    """Flatten the evidence security check map (HP01 honeypot, CR mintable…)."""
    security = evidence.get("security")
    if not isinstance(security, dict):
        return {}
    checks = security.get("checks")
    if not isinstance(checks, dict):
        return {}
    flat: dict[str, Any] = {}
    for code, detail in checks.items():
        if isinstance(detail, dict):
            flat[str(code)] = {
                "status": detail.get("status"),
                "details": str(detail.get("details") or ""),
            }
        else:
            flat[str(code)] = {"status": None, "details": str(detail or "")}
    return flat


def _honeypot_verdict(evidence: dict[str, Any]) -> str:
    """Honeypot verdict from the evidence security checks (HP01 = honeypot)."""
    checks = _security_checks(evidence)
    hp = checks.get("HP01") or checks.get("HP04")
    if not isinstance(hp, dict):
        return "unknown"
    details = str(hp.get("details") or "").lower()
    status = hp.get("status")
    if status == "fail" or "honeypot=yes" in details or "honeypot: yes" in details:
        return "detected"
    if status == "pass" and "no" in details:
        return "none"
    return "unknown"


def _flatten_v2(body: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Map the unified v2 scan payload into the chat-presentable intel block."""
    evidence = body.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    sentinel = body.get("sentinel")
    if not isinstance(sentinel, dict):
        sentinel = {}
    free = sentinel.get("free")
    if not isinstance(free, dict):
        free = {}
    holder_analysis = free.get("holder_analysis")
    if not isinstance(holder_analysis, dict):
        holder_analysis = {}
    liquidity_verification = free.get("liquidity_verification")
    if not isinstance(liquidity_verification, dict):
        liquidity_verification = {}
    funding_analysis = evidence.get("funding_analysis")
    if not isinstance(funding_analysis, dict):
        funding_analysis = {}
    holder_overlap = evidence.get("holder_overlap")
    if not isinstance(holder_overlap, dict):
        holder_overlap = {}
    market = evidence.get("market")
    if not isinstance(market, dict):
        market = {}
    bundle = body.get("bundle_analysis")
    if not isinstance(bundle, dict):
        bundle = {}
    wallet_metrics = body.get("wallet_metrics")
    if not isinstance(wallet_metrics, dict):
        wallet_metrics = {}

    safety = _safe_float(body.get("safety_score"))
    risk_score = round(100.0 - safety, 2)

    return {
        "source": "backend:/api/v2/scanner/token/scan",
        "status": body.get("status"),
        "scanned_at": body.get("scanned_at"),
        "cached": bool(body.get("cached")),
        "tier": body.get("tier"),
        "background_status": body.get("background_status"),
        "fallback_used": bool(body.get("fallback_used")),
        "modules_run": body.get("modules_run"),
        "scan_duration_ms": body.get("scan_duration_ms"),
        # Risk
        "safety_score": safety,
        "risk_score": risk_score,
        "risk_level": body.get("risk_level") or "unknown",
        "confidence": body.get("confidence"),
        "risk_flags": body.get("risk_flags") or [],
        "green_flags": body.get("green_flags") or [],
        "breakdown": body.get("breakdown"),
        # Honeypot (from our security evidence checks)
        "honeypot": _honeypot_verdict(evidence),
        "security_checks": _security_checks(evidence),
        # Bundle detection (own ClickHouse engine)
        "bundle_analysis": {
            "is_bundled": bundle.get("is_bundled"),
            "risk_label": bundle.get("risk_label"),
            "confidence": bundle.get("confidence"),
            "launch_cluster_score": bundle.get("launch_cluster_score"),
            "deployer_funded_pct": bundle.get("deployer_funded_pct"),
            "first_time_buyer_pct": bundle.get("first_time_buyer_pct"),
        },
        # Wash-trading proxy (own volume-authenticity engine)
        "wash_trading": {
            "fake_volume_pct": market.get("fake_volume_pct"),
        },
        # Holder analysis (own engines + SENTINEL)
        "holder_analysis": {
            "total_holders": holder_analysis.get("total_holders"),
            "hhi": holder_analysis.get("hhi"),
            "hhi_risk_level": holder_analysis.get("risk_level"),
            "top_10_pct": holder_analysis.get("top_10_percentage"),
            "top_20_pct": holder_analysis.get("top_20_percentage"),
            "top_50_pct": holder_analysis.get("top_50_percentage"),
            "fake_diversification_score": holder_analysis.get("fake_diversification_score"),
            "exchange_funded_pct": holder_analysis.get("exchange_funded_percentage"),
            "new_wallets_pct": holder_analysis.get("new_wallets_percentage"),
            "warnings": holder_analysis.get("warnings"),
        },
        "wallet_metrics": wallet_metrics,
        "funding_analysis": funding_analysis,
        "holder_overlap": holder_overlap,
        # Liquidity verification (SENTINEL module)
        "liquidity_verification": {
            "total_locked_pct": liquidity_verification.get("total_locked_percentage"),
            "lp_burned": liquidity_verification.get("lp_burned"),
            "fake_lock_detected": liquidity_verification.get("fake_lock_detected"),
            "risk_level": liquidity_verification.get("risk_level"),
        },
        # Deployer intel
        "deployer_profile": body.get("deployer_profile"),
        # Market
        "price_usd": body.get("price_usd"),
        "market_cap_usd": body.get("market_cap_usd"),
        "liquidity_usd": body.get("liquidity_usd"),
        "volume_24h_usd": body.get("volume_24h_usd"),
        "age_hours": body.get("age_hours"),
        # Enrichment
        "news_sentiment": body.get("news_sentiment"),
        "news_card": body.get("news_card"),
        "rag_context": body.get("rag_context"),
        "data_sources": body.get("data_sources"),
        "sentinel_premium_preview": sentinel.get("premium_preview"),
    }


def _flatten_v1(body: dict[str, Any], address: str, chain: str) -> dict[str, Any]:
    """Map the fast bot scan payload into the same intel block shape."""
    return {
        "source": "backend:/api/v1/bot/scan",
        "status": "ok",
        "scanned_at": body.get("scanned_at"),
        "cached": bool(body.get("cached")),
        "tier": "free",
        "background_status": "complete",
        "fallback_used": False,
        "modules_run": ["fast_bot_scan"],
        "scan_duration_ms": None,
        # Risk (quick scan exposes a coarse 0-100 score)
        "safety_score": round(100.0 - _safe_float(body.get("risk_score")), 2),
        "risk_score": body.get("risk_score"),
        "risk_level": "unknown",
        "confidence": None,
        "risk_flags": [],
        "green_flags": [],
        "breakdown": None,
        "honeypot": "detected" if body.get("is_honeypot") else "none",
        "security_checks": {
            "HP01": {"status": "fail" if body.get("is_honeypot") else "pass", "details": ""},
            "CR03": {"status": "fail" if body.get("is_mintable") else "pass", "details": "Mintable"},
        },
        "bundle_analysis": None,
        "wash_trading": {"fake_volume_pct": None},
        "holder_analysis": {
            "total_holders": body.get("holders"),
            "hhi": None,
            "hhi_risk_level": None,
            "top_10_pct": None,
            "top_20_pct": None,
            "top_50_pct": None,
            "fake_diversification_score": None,
            "exchange_funded_pct": None,
            "new_wallets_pct": None,
            "warnings": None,
        },
        "wallet_metrics": None,
        "funding_analysis": {"deployer_funded_by": body.get("deployer_funded_by")},
        "holder_overlap": None,
        "liquidity_verification": None,
        "deployer_profile": None,
        "price_usd": body.get("price_usd"),
        "market_cap_usd": None,
        "liquidity_usd": body.get("liquidity_usd"),
        "volume_24h_usd": body.get("volume_24h"),
        "age_hours": None,
        "news_sentiment": None,
        "news_card": None,
        "rag_context": None,
        "data_sources": None,
        "sentinel_premium_preview": None,
    }


def _flatten_dexscreener(address: str, chain: str) -> dict[str, Any]:
    """Fallback scan shape from DexScreener pairs (risk fields unavailable)."""
    return {
        "source": "dexscreener:/latest/dex/tokens",
        "status": "ok",
        "scanned_at": None,
        "cached": False,
        "tier": "free",
        "background_status": "complete",
        "fallback_used": True,
        "modules_run": ["dexscreener_fallback"],
        "scan_duration_ms": None,
        "safety_score": None,
        "risk_score": None,
        "risk_level": "unavailable",
        "confidence": None,
        "risk_flags": [],
        "green_flags": [],
        "breakdown": None,
        "honeypot": "unknown",
        "security_checks": {},
        "bundle_analysis": None,
        "wash_trading": {"fake_volume_pct": None},
        "holder_analysis": None,
        "wallet_metrics": None,
        "funding_analysis": None,
        "holder_overlap": None,
        "liquidity_verification": None,
        "deployer_profile": None,
        "price_usd": None,
        "market_cap_usd": None,
        "liquidity_usd": None,
        "volume_24h_usd": None,
        "age_hours": None,
        "news_sentiment": None,
        "news_card": None,
        "rag_context": None,
        "data_sources": None,
        "sentinel_premium_preview": None,
    }


async def scan_token(address: str, chain: str | None = None) -> dict[str, Any]:
    """Legacy multi-source token scan (DexScreener + GoPlus + Honeypot.is +
    RMI databus). Used as the /scan fallback when the unified v2 scan
    (``scan_token_v2``) is unavailable, and by wallet/investigate/payments.
    """
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

    client = get_shared_client(15)
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
                # Null-safe numeric coercion: a null priceUsd (delisted /
                # no-pair payloads) must never crash the scan reply.
                result["price"] = _safe_float(p.get("priceUsd"))
                result["mcap"] = _safe_float(p.get("marketCap") or p.get("fdv") or 0)
                result["fdv"] = _safe_float(p.get("fdv") or result["mcap"])
                vol = p.get("volume", {})
                result["volume_24h"] = _safe_float(vol.get("h24") or 0)
                liq = p.get("liquidity", {})
                result["liquidity"] = _safe_float(liq.get("usd") or 0)
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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
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
                    result["buy_tax"] = _safe_float(sec.get("buy_tax"))
                    result["sell_tax"] = _safe_float(sec.get("sell_tax"))
                    result["mintable"] = sec.get("is_mintable") == "1"
                    result["can_blacklist"] = (
                        sec.get("is_blacklisted") == "1" or sec.get("can_take_back_ownership") == "1"
                    )
                    result["owner_renounced"] = sec.get("is_owner_renounced") != "0"
                    lp_locks = sec.get("lp_holders", [])
                    locked_pct = sum(_safe_float(h.get("percent")) for h in lp_locks if h.get("is_locked") == 1)
                    result["lp_locked"] = locked_pct
                    holders_data = sec.get("holders", [])
                    result["holders"] = len(holders_data) if holders_data else 0
                    for h in holders_data[:10]:
                        result["top_holders"].append(
                            {
                                "address": h.get("address", ""),
                                "pct": _safe_float(h.get("percent")) * 100,
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
                        result["threats"].append(f"High buy tax: {result['buy_tax'] * 100:.1f}%")
                        result["risk_score"] += 10
                    if result["sell_tax"] and result["sell_tax"] > 0.1:
                        result["threats"].append(f"High sell tax: {result['sell_tax'] * 100:.1f}%")
                        result["risk_score"] += 15
                    if locked_pct < 0.5 and result["mcap"] > 10000:
                        result["threats"].append(f"Low LP lock: {locked_pct * 100:.0f}%")
                        result["risk_score"] += 15
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
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
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            result["errors"].append(f"Honeypot.is: {e}")

    # RMI Backend
    try:
        r = await client.get(f"{BACKEND_URL}/api/v1/databus/token/{address}", headers=backend_headers(), timeout=10)
        if r.status_code == 200:
            rmi = r.json()
            if rmi.get("bundle_detected"):
                result["bundle_detected"] = True
                result["threats"].append("Bundle activity detected")
                result["risk_score"] += 20
            if rmi.get("fresh_wallet_pct"):
                result["fresh_wallet_pct"] = rmi["fresh_wallet_pct"]
                if rmi["fresh_wallet_pct"] > 50:
                    result["threats"].append(f"High fresh wallet ratio: {rmi['fresh_wallet_pct']:.0f}%")
                    result["risk_score"] += 10
            if rmi.get("dev_wallet"):
                result["dev_wallet"] = rmi["dev_wallet"]
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        result["errors"].append(f"RMI Backend: {e}")

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
    # (hard 6s internal budget, fail-open; never raises).
    result = await enrich_scan_with_gmgn(result, address, result["chain"])
    # Risk-signal enrichment (bundle / deployer reputation / wash trading /
    # whale flow / authority / social cross-ref) — best-effort + non-blocking
    # (hard 8s budget, fail-open per module; never raises). Attaches
    # ``risk_signals`` so the formatter can render the "why it's sus" block.
    if enrich_scan is not None:
        result = await enrich_scan(result, address, result["chain"])
    # Scan-to-RAG memory: auto-index the summary into Qdrant so future
    # chat queries can recall "have we scanned this token before?" with
    # risk context. Background + fail-open — never blocks the response.
    if should_index_scan(result, source="scan_v1"):
        schedule_scan_memory_index(result, source="scan_v1")
    return result


async def scan_token_v2(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """Canonical v2 UnifiedTokenScanner — explainable multi-dimensional scoring.

    POSTs to ``/api/v2/scanner/token/scan`` through the tools' shared
    ``backend_json_post`` client (the SAME client ``scanner_intel`` uses —
    unified 2026-08-15; the legacy scanner previously built its own
    ``httpx.AsyncClient`` for this exact endpoint). Returns the raw v2
    payload (safety_score, confidence, risk_level, risk_flags, green_flags,
    breakdown, evidence, modules_run). Returns None on any failure so
    callers can fall back to the legacy scanner.
    """
    chain_norm = chain or detect_chain(address)
    data = await backend_json_post(
        "/api/v2/scanner/token/scan",
        json={"address": address, "chain": chain_norm},
        timeout=45.0,
    )
    if data is None:
        logger.warning("scan_token_v2 failed for %s", address)
        return None
    if data.get("status") != "ok":
        logger.warning("scan_token_v2 non-ok status for %s", address)
        return None
    # GMGN holder/smart-money enrichment — best-effort + non-blocking
    # (hard 6s internal budget, fail-open; never raises).
    data = await enrich_scan_with_gmgn(data, address, chain_norm)
    # Risk-signal enrichment — best-effort + non-blocking, see scan_token.
    if enrich_scan is not None:
        data = await enrich_scan(data, address, chain_norm)
    # Scan-to-RAG memory (background, fail-open) — see scan_token.
    if should_index_scan(data, source="scan_v2"):
        schedule_scan_memory_index(data, source="scan_v2")
    return data


async def sentinel_scan(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """SENTINEL deep forensic scan — POST /api/v1/x402-tools/sentinel_scan.

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
    hard dependency). Kept on the raw httpx client because ``SENTINEL_URL``
    may be env-overridden to a non-backend host.
    """
    chain_norm = chain or detect_chain(address)
    try:
        client = get_shared_client(SENTINEL_TIMEOUT)
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
    try:
        params: dict[str, str] = {"address": address}
        if chain:
            params["chain"] = chain
        payload = await http_get_json(
            f"{BACKEND_URL}/api/v1/databus/fetch/hype_score",
            params=params,
            headers=backend_headers(),
            timeout=20,
        )
        if payload is None:
            logger.warning("fetch_hype_score failed for %s", address)
            return None
        data = payload.get("data") or payload
        if not isinstance(data, dict) or data.get("result") != "scanned":
            logger.warning("fetch_hype_score non-scanned result for %s", address)
            return None
        return data
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("fetch_hype_score failed for %s: %s", address, e)
        return None


async def fetch_deployer_fingerprint(deployer: str, chain: str | None = None) -> dict[str, Any] | None:
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
    try:
        params: dict[str, str] = {"deployer": deployer}
        if chain:
            params["chain"] = chain
        payload = await http_get_json(
            f"{BACKEND_URL}/api/v1/databus/fetch/deployer_fingerprint",
            params=params,
            headers=backend_headers(),
            timeout=20,
        )
        if payload is None:
            logger.warning("fetch_deployer_fingerprint failed for %s", deployer)
            return None
        data = payload.get("data") or payload
        if not isinstance(data, dict) or data.get("result") != "scanned":
            logger.warning("fetch_deployer_fingerprint non-scanned result for %s", deployer)
            return None
        return data
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning("fetch_deployer_fingerprint failed for %s: %s", deployer, e)
        return None


async def analyze_wallet_v2(address: str, chain: str | None = None) -> dict[str, Any] | None:
    """v2 UnifiedWalletScanner — entity clustering, scam exposure, smart money.

    POSTs to ``/api/v2/scanner/wallet/scan`` through the tools' shared
    ``backend_json_post`` client (same client family as ``scan_token_v2``).
    Returns the raw v2 payload (entity_name, entity_type, persona,
    scam_exposure_pct, smart_money_overlap, behavior_patterns, risk_forecast,
    risk_score, risk_flags). Returns None on failure so callers can fall back
    to the legacy wallet analyzer.
    """
    chain_norm = chain or detect_chain(address)
    data = await backend_json_post(
        "/api/v2/scanner/wallet/scan",
        json={"address": address, "chain": chain_norm},
        timeout=45.0,
    )
    if data is None:
        logger.warning("analyze_wallet_v2 failed for %s", address)
        return None
    if data.get("status") != "ok":
        logger.warning("analyze_wallet_v2 non-ok status for %s", address)
        return None
    return data


async def analyze_wallet(address: str, chain: str | None = None) -> dict[str, Any]:
    """Legacy wallet analyzer — RMI databus wallet tokens + Solana.fm fallback.

    Used as the /wallet fallback when the unified v2 wallet scan
    (``analyze_wallet_v2``) is unavailable.
    """
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
    client = get_shared_client(15)
    try:
        r = await client.get(
            f"{BACKEND_URL}/api/v1/databus/wallet/{address}/tokens", headers=backend_headers(), timeout=10
        )
        if r.status_code == 200:
            data = r.json()
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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"analyze_wallet RMI backend failed for {address}: {e}")
    if is_sol(address):
        try:
            r = await client.get(f"{SOLANAFM_API}/accounts/{address}")
            if r.status_code == 200:
                data = r.json()
                sol_price = await get_sol_price()
                # Null-safe: a null Solana.fm balance must not crash /wallet.
                result["balance_usd"] = _safe_float(data.get("balance")) / 1e9 * sol_price
                result["tx_count"] = data.get("transaction_count", 0)
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            result["errors"].append(f"Solana.fm: {e}")
    return result


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
    safety = _safe_float(data.get("safety_score"))

    return {
        "address": data.get("token", ""),
        "chain": data.get("chain", "solana"),
        "name": data.get("name") or "Unknown",
        "symbol": data.get("symbol") or "???",
        "price": _safe_float(data.get("price_usd")),
        "mcap": _safe_float(data.get("market_cap_usd")),
        "fdv": _safe_float(data.get("market_cap_usd")),
        "volume_24h": _safe_float(data.get("volume_24h_usd")),
        "liquidity": _safe_float(data.get("liquidity_usd")),
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
        "lp_locked": (
            _safe_float(liquidity.get("locked_pct")) / 100 if liquidity.get("locked_pct") is not None else None
        ),
        "mintable": None,
        "can_blacklist": None,
        "owner_renounced": None,
        "top_holders": [],
        "bundle_detected": "bundle" in " ".join(risks).lower(),
        # NOTE: "fresh_wallet_pct" is set once, below, from the v2 funding
        # analysis (the legacy converter's duplicate 0-default was dead code —
        # the funding value always won in dict order).
        "exchange_pct": 0,
        "volume_real_ratio": (
            _safe_float(mkt.get("fake_volume_pct")) if mkt.get("fake_volume_pct") is not None else None
        ),
        "dev_wallet": dev.get("deployer_address") or dev.get("address") or None,
        "dev_other_tokens": [],
        "errors": [],
        "socials": {},
        # v2 explainable scoring
        "safety_score": safety,
        "confidence": _safe_float(data.get("confidence")),
        "risk_level": data.get("risk_level") or "unknown",
        "green_flags": green,
        "breakdown": data.get("breakdown"),
        "evidence": evidence,
        "modules_run": list(data.get("modules_run") or []),
        # v2 funding analysis (exchange-funded / fresh wallets / scam overlap)
        "exchange_funded_pct": _safe_float(funding.get("exchange_funded_pct")),
        "fresh_wallet_pct": _safe_float(funding.get("fresh_wallet_pct")),
        "holder_overlap_pct": _safe_float(overlap.get("max_overlap_pct")),
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
        "balance_usd": _safe_float(data.get("total_value_usd")),
        "tx_count": int(data.get("token_count") or 0),
        "first_seen": None,
        "wallet_age_days": 0,
        "top_tokens": [],
        "is_fresh": False,
        "is_exchange": (data.get("entity_type") or "").lower() == "exchange",
        "is_contract": (data.get("entity_type") or "").lower() == "contract",
        "risk_score": _safe_float(data.get("risk_score")),
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
        "scam_exposure_pct": _safe_float(data.get("scam_exposure_pct")),
        "smart_money_overlap": list(data.get("smart_money_overlap") or []),
        "behavior_patterns": list(data.get("behavior_patterns") or []),
        "risk_forecast": data.get("risk_forecast") or "",
        "modules_run": list(data.get("modules_run") or []),
        # v2 funding analysis — the wallet's exposure to scams IS its
        # holder-overlap-with-known-scams metric. Exchange/fresh percentages
        # are carried through defensively when the backend provides them.
        "exchange_funded_pct": data.get("exchange_funded_pct"),
        "fresh_wallet_pct": data.get("fresh_wallet_pct"),
        "holder_overlap_pct": _safe_float(data.get("scam_exposure_pct")),
    }


async def scanner_intel(*, address: str = "", chain: str = "") -> ToolResult:
    """Real scanner intel for a token — OUR scanner is the primary source.

    Call order: unified v2 scan → fast bot scan → DexScreener fallback (see
    module docstring). Every payload embeds the ``token`` identity block
    (logo + name + symbol + chain + price) resolved by ``token_meta``.
    """
    if not address:
        return ToolResult(tool="scanner_intel", ok=False, error="address required")

    chain = (chain or "").strip() or _detect_chain(address)

    # 1. Own backend unified v2 scan (primary source — best-in-class scanner).
    #    Delegates to scan_token_v2 so /scan and the chat tool share ONE v2
    #    engine — one client, one payload shape (unified 2026-08-15).
    body = await scan_token_v2(address, chain)
    # Gate: the unified scanner returns ``status: ok`` even for addresses with
    # no on-chain token (empty name/symbol, zero price, bogus flags). Require
    # real token identity before treating it as a genuine scan result, else we
    # report fabricated "medium risk" for a token that does not exist.
    if isinstance(body, dict) and (body.get("name") or body.get("symbol")):
        intel = _flatten_v2(body, address, chain)
        meta = await _token_meta_dict(address, chain, symbol=str(body.get("symbol") or ""))
        intel["token"] = meta
        return ToolResult(
            tool="scanner_intel",
            ok=True,
            data=intel,
            symbols=[str(body.get("symbol") or meta.get("symbol") or "")]
            if (body.get("symbol") or meta.get("symbol"))
            else [],
            addresses=[address],
            source="backend:/api/v2/scanner/token/scan",
        )

    # 2. Own backend fast bot scan (Redis-cached quick scan).
    body = await backend_json_post(
        "/api/v1/bot/scan",
        json={"address": address, "chain": chain},
        timeout=10.0,
    )
    if isinstance(body, dict) and body.get("risk_score") is not None and not body.get("error"):
        meta = await _token_meta_dict(address, chain)
        # v1 serves whatever sits in the scanner cache — including a garbage
        # entry persisted by a failed v2 attempt on a non-existent address.
        # Require real token identity (resolved name/symbol) before presenting.
        if meta.get("name") or meta.get("symbol"):
            intel = _flatten_v1(body, address, chain)
            intel["token"] = meta
            return ToolResult(
                tool="scanner_intel",
                ok=True,
                data=intel,
                symbols=[str(meta.get("symbol"))] if meta.get("symbol") else [],
                addresses=[address],
                source="backend:/api/v1/bot/scan",
            )

    # 3. DexScreener direct — external fallback when our backend is down.
    body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
    pairs = (body or {}).get("pairs") if isinstance(body, dict) else None
    if isinstance(pairs, list) and pairs:
        intel = _flatten_dexscreener(address, chain)
        meta = await _token_meta_dict(address, chain)
        intel["token"] = meta
        return ToolResult(
            tool="scanner_intel",
            ok=True,
            data=intel,
            symbols=[str(meta.get("symbol"))] if meta.get("symbol") else [],
            addresses=[address],
            source="dexscreener:/latest/dex/tokens",
        )

    return ToolResult(
        tool="scanner_intel",
        ok=False,
        error="no live scanner data (backend + DexScreener unavailable)",
        addresses=[address],
    )


def register_extra_tools() -> None:
    """Register scanner_intel + token_meta on the shared tool registry.

    Called on import (self-registration) so the tools package wires them in
    with a single additive import line in ``tools/__init__.py``.
    """
    from rugmunchbot.tools import registry

    registry.register("scanner_intel", scanner_intel, timeout=35.0, ttl=120.0)
    registry.register("token_meta", token_meta, timeout=12.0, ttl=300.0)


# Self-registration: importing this module wires the extra tools into the
# shared registry singleton (see tools/__init__.py additive import).
register_extra_tools()
