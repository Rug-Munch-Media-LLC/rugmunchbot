# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/insider_detector/scoring.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Public API — detect_insiders, get_insider_score, find_pre_pump_wallets."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines import gmgn_client as gmgn

from .collectors import _collect_token_meta, _gather_events, _get_token_traders
from .config import INSIDER_WINDOW_MIN, MIN_INSIDER_BUY_USD
from .detectors import (
    _detect_cex_frontrun,
    _detect_dev_insider,
    _detect_pre_kol,
    _detect_pre_trending,
    _detect_pumpfun_graduation,
    _detect_snipers,
)
from .models import InsiderReport
from .utils import _check_early_buy, _deduplicate_evidence, _generate_insights

logger = get_logger(__name__)


async def detect_insiders(
    token_address: str,
    chain: str = "sol",
    deployer: str | None = None,
    max_traders: int = 100,
) -> InsiderReport:
    """Detect ALL insider wallets for a given token.
    Runs every detection method in parallel and aggregates evidence.
    """
    report = InsiderReport(
        token=token_address,
        token_symbol=token_address[:10],
        chain=chain,
        deployer=deployer or "unknown",
    )

    # Phase 1: Collect token metadata + deployer in parallel
    symbol, deployer_addr, token_created_at = await _collect_token_meta(token_address, chain, deployer)
    report.token_symbol = symbol or token_address[:10]
    if deployer_addr:
        report.deployer = deployer_addr

    # Phase 2: Gather traders and trades for this token
    traders = await _get_token_traders(token_address, chain, max_traders)
    if not traders:
        logger.info("No traders found for %s on %s", token_address, chain)
        return report

    # Phase 3: Gather event timestamps in parallel
    events = await _gather_events(token_address, chain, deployer_addr, symbol, token_created_at)

    # Phase 4: Run all detection methods
    detectors = [
        _detect_pre_trending(token_address, chain, traders, events),
        _detect_pre_kol(token_address, chain, traders, events),
        _detect_dev_insider(token_address, chain, traders, events, deployer_addr),
        _detect_cex_frontrun(token_address, chain, traders, events),
        _detect_pumpfun_graduation(token_address, chain, traders, events),
        _detect_snipers(token_address, chain, traders, events, token_created_at),
    ]

    results = await asyncio.gather(*detectors, return_exceptions=True)

    for r in results:
        if isinstance(r, BaseException):
            logger.warning("Detection method failed: %s", r, exc_info=True)
            continue
        for evidence in r:
            report.add(evidence)

    # Phase 5: Deduplicate by wallet + type, keep highest confidence
    report.insiders = _deduplicate_evidence(report.insiders)

    # Phase 6: Generate insights
    report.insights = _generate_insights(report)

    logger.info(
        "Insider detection complete for %s: %d insiders, $%.0f frontrun value",
        token_address,
        len(report.insiders),
        report.total_frontrun_value,
    )

    return report


async def get_insider_score(wallet: str, chain: str = "sol") -> dict[str, Any]:
    """Score a wallet on how likely it is to be an insider (0-100)."""
    score = 0.0
    signals = []

    try:
        wallet_trades = gmgn.get_wallet_trades(wallet, chain=chain, limit=100)
    except (ValueError, TypeError, KeyError, OSError) as e:
        logger.debug("get_insider_score_error", exc_info=True)
        logger.debug("GMGN wallet trades failed: %s", e)
        wallet_trades = []

    if not wallet_trades:
        return {
            "wallet": wallet,
            "insider_score": 0,
            "insider_risk": "UNKNOWN",
            "signals": ["No trade data available"],
            "pre_event_buys": 0,
            "avg_lead_time_minutes": 0,
            "total_frontrun_profit": 0,
        }

    pre_event_count = 0
    total_lead_time = 0.0
    total_profit = 0.0

    for trade in wallet_trades[:100]:
        bought_at = trade.get("timestamp", 0)
        if not bought_at:
            continue
        token_addr = trade.get("token_address", "")
        side = trade.get("side", "buy")
        if side.lower() != "buy":
            continue
        bought_usd = float(trade.get("amount_usd", 0))
        if bought_usd < MIN_INSIDER_BUY_USD:
            continue
        tx_hash = trade.get("tx_hash", "")
        if not tx_hash:
            continue
        is_early, lead_time, evidence = await _check_early_buy(token_addr, chain, bought_at, bought_usd, tx_hash)
        if is_early:
            pre_event_count += 1
            total_lead_time += lead_time
            total_profit += evidence.pnl_estimate if evidence else 0

    if pre_event_count > 0:
        avg_lead = total_lead_time / pre_event_count if pre_event_count > 0 else 0
        score = min(100, 25 + min(pre_event_count * 12, 35) + min(avg_lead / 10, 20) + min(total_profit / 10000, 20))
        signals.append(f"{pre_event_count} pre-event buys detected")
        if pre_event_count >= 3:
            signals.append("Pattern: consistent early buyer across multiple tokens")
            score = min(100, score + 10)
        if avg_lead > 120:
            signals.append("Long average lead time suggests non-public information")
        if total_profit > 50000:
            signals.append(f"Estimated ${total_profit:,.0f} profit from early buys")
            score = min(100, score + 10)

    try:
        tags = gmgn.get_wallet_tags(wallet, chain)
        if "smart_degen" in tags:
            signals.append("Tagged as smart_degen by GMGN")
            score = min(100, score + 5)
        if "photon" in tags:
            signals.append("Photon/automated trader pattern")
    except (ValueError, TypeError, KeyError, OSError):
        logger.debug("unknown_error", exc_info=True)
        logger.warning("get_wallet_tags failed", exc_info=True)

    risk = "UNKNOWN"
    if score >= 70:
        risk = "CRITICAL"
    elif score >= 50:
        risk = "HIGH"
    elif score >= 25:
        risk = "MEDIUM"
    elif score > 0:
        risk = "LOW"

    return {
        "wallet": wallet,
        "insider_score": round(score),
        "insider_risk": risk,
        "signals": signals,
        "pre_event_buys": pre_event_count,
        "avg_lead_time_minutes": round(total_lead_time / pre_event_count, 1) if pre_event_count else 0,
        "total_frontrun_profit": round(total_profit, 2),
    }


async def find_pre_pump_wallets(token_address: str, chain: str = "sol") -> list[dict[str, Any]]:
    """Find wallets that bought this token BEFORE the pump."""
    report = await detect_insiders(token_address, chain)
    results = []
    for ev in report.insiders:
        if ev.lead_time_minutes >= INSIDER_WINDOW_MIN:
            results.append(
                {
                    "wallet": ev.wallet,
                    "type": ev.insider_type,
                    "lead_time_minutes": round(ev.lead_time_minutes, 1),
                    "bought_amount_usd": round(ev.bought_amount_usd, 2),
                    "pnl_estimate": round(ev.pnl_estimate, 2),
                    "confidence": ev.confidence,
                    "evidence": ev.evidence,
                }
            )
    results.sort(key=lambda x: cast("float", x["lead_time_minutes"]), reverse=True)
    return results
