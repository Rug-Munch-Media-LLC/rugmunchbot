"""scanner_intel tool — RugMunch's best-in-class scanner as the PRIMARY source.

This tool feeds the chat bot from OUR own scanner engines — the "best in
class scanner" differentiator — and exposes as much of the payload as the bot
can present: risk score, honeypot verdicts, bundle detection, wash-trading
proxy, rug-level verdict and holder analysis.

Call order (own data first, automatic fallback when our backend is down or
rate-limited — see module docstring of ``tools/token_meta.py`` for the
identity/image resolution order):

  1. Own backend unified v2 scan (``POST /api/v2/scanner/token/scan``) — the
     50+ module Advanced-unified scan: safety_score, confidence, risk_level,
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

from typing import Any

from rugmunchbot.config import DEXSCREENER
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._client import http_get_json
from rugmunchbot.tools.registry import ToolResult, backend_json_post
from rugmunchbot.tools.token_meta import resolve_token_meta, token_meta

logger = get_logger("rmi_bot.tools.scanner")


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
    advanced = body.get("sentinel")
    if not isinstance(advanced, dict):
        advanced = {}
    free = advanced.get("free")
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

    safety = float(body.get("safety_score") or 0)
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
        # Holder analysis (own engines + Advanced)
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
        # Liquidity verification (Advanced module)
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
        "advanced_premium_preview": advanced.get("premium_preview"),
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
        "safety_score": round(100.0 - float(body.get("risk_score") or 0), 2),
        "risk_score": body.get("risk_score"),
        "risk_level": "unknown",
        "confidence": None,
        "risk_flags": [],
        "green_flags": [],
        "breakdown": None,
        "honeypot": "detected" if body.get("is_honeypot") else "none",
        "security_checks": {
            "HP01": {"status": "fail" if body.get("is_honeypot") else "pass", "details": ""},
            "CR03": {
                "status": "fail" if body.get("is_mintable") else "pass",
                "details": "Mintable",
            },
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
        "advanced_premium_preview": None,
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
        "advanced_premium_preview": None,
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
    body = await backend_json_post(
        "/api/v2/scanner/token/scan",
        json={"address": address, "chain": chain},
        timeout=30.0,
    )
    # Gate: the unified scanner returns ``status: ok`` even for addresses with
    # no on-chain token (empty name/symbol, zero price, bogus flags). Require
    # real token identity before treating it as a genuine scan result, else we
    # report fabricated "medium risk" for a token that does not exist.
    if (
        isinstance(body, dict)
        and body.get("status") == "ok"
        and (body.get("name") or body.get("symbol"))
    ):
        intel = _flatten_v2(body, address, chain)
        meta = await resolve_token_meta(
            address=address, chain=chain, symbol=str(body.get("symbol") or "")
        )
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
        meta = await resolve_token_meta(address=address, chain=chain)
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
        meta = await resolve_token_meta(address=address, chain=chain)
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
