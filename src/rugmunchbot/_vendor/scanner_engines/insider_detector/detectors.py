# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/insider_detector/detectors.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Phase 2 Detection Methods — All insider detection algorithms."""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines import gmgn_client as gmgn
from rugmunchbot._vendor.scanner_engines.known_entities import label_wallet

from .config import (
    AVG_KOL_PUMP,
    AVG_PRE_TRENDING_PUMP,
    AVG_PUMPFUN_GRADUATION_PUMP,
    CEX_LISTING_MULTIPLIER,
    INSIDER_WINDOW_MIN,
    MIN_INSIDER_BUY_USD,
    PRE_CEX_WINDOW_MAX,
    PRE_GRADUATION_WINDOW_MAX,
    PRE_KOL_WINDOW_MAX,
    PRE_TRENDING_WINDOW_MAX,
)
from .helpers import _ts_human
from .models import Confidence, InsiderEvidence, InsiderType

logger = get_logger(__name__)


async def _detect_pre_trending(
    token_address: str,
    chain: str,
    traders: list[dict[str, Any]],
    events: dict[str, Any],
) -> list[InsiderEvidence]:
    """Detect wallets that bought before the token appeared on trending."""
    results: list[InsiderEvidence] = []
    trending_at = events.get("dexscreener_first_seen") or events.get("gmgn_first_trending")
    if not trending_at:
        return results
    event_desc = (
        events.get("dexscreener_first_seen_desc")
        or events.get("gmgn_first_trending_desc")
        or "Token appeared on trending"
    )

    for trader in traders:
        first_buy = trader.get("first_buy_at", 0)
        if not first_buy or first_buy >= trending_at:
            continue
        lead_time = (trending_at - first_buy) / 60
        if lead_time < INSIDER_WINDOW_MIN or lead_time > PRE_TRENDING_WINDOW_MAX:
            continue
        total_bought = trader.get("total_bought_usd", 0)
        if total_bought < MIN_INSIDER_BUY_USD:
            continue
        pnl = total_bought * AVG_PRE_TRENDING_PUMP
        confidence = Confidence.HIGH
        if lead_time > 120:
            confidence = Confidence.CRITICAL if lead_time > 360 else Confidence.HIGH
        elif lead_time < 30:
            confidence = Confidence.MEDIUM
        results.append(
            InsiderEvidence(
                wallet=trader["wallet"],
                insider_type=InsiderType.PRE_TRENDING,
                bought_at=first_buy,
                bought_amount_usd=total_bought,
                event=f"{event_desc} at {_ts_human(trending_at)} UTC",
                event_at=trending_at,
                lead_time_minutes=lead_time,
                pnl_estimate=pnl,
                evidence=(
                    f"Bought {total_bought:+.0f} USDT {lead_time:.0f}min before trending"
                    if first_buy and trending_at
                    else "Pre-trending buy detected"
                ),
                confidence=confidence,
            )
        )
    return results


async def _detect_pre_kol(
    token_address: str,
    chain: str,
    traders: list[dict[str, Any]],
    events: dict[str, Any],
) -> list[InsiderEvidence]:
    """Detect wallets that bought before the first KOL/influencer trade."""
    results: list[InsiderEvidence] = []
    first_kol_at = events.get("first_kol_trade_at")
    if not first_kol_at:
        return results
    kol_desc = events.get("first_kol_trade_desc", "First KOL trade")

    for trader in traders:
        first_buy = trader.get("first_buy_at", 0)
        if not first_buy or first_buy >= first_kol_at:
            continue
        lead_time = (first_kol_at - first_buy) / 60
        if lead_time < INSIDER_WINDOW_MIN or lead_time > PRE_KOL_WINDOW_MAX:
            continue
        total_bought = trader.get("total_bought_usd", 0)
        if total_bought < MIN_INSIDER_BUY_USD:
            continue
        pnl = total_bought * AVG_KOL_PUMP
        confidence = Confidence.HIGH
        if lead_time > 120:
            confidence = Confidence.CRITICAL
        elif lead_time < 30:
            confidence = Confidence.MEDIUM
        results.append(
            InsiderEvidence(
                wallet=trader["wallet"],
                insider_type=InsiderType.PRE_KOL,
                bought_at=first_buy,
                bought_amount_usd=total_bought,
                event=kol_desc,
                event_at=first_kol_at,
                lead_time_minutes=lead_time,
                pnl_estimate=pnl,
                evidence=f"Bought {total_bought:+.0f} USDT {lead_time:.0f}min before {kol_desc}",
                confidence=confidence,
            )
        )
    return results


async def _detect_dev_insider(
    token_address: str,
    chain: str,
    traders: list[dict[str, Any]],
    events: dict[str, Any],
    deployer: str | None,
) -> list[InsiderEvidence]:
    """Detect wallets connected to the deployer that bought early."""
    results: list[InsiderEvidence] = []
    if not deployer:
        return results

    deployer_lower = deployer.lower()
    created_at = events.get("deployer_created_at") or events.get("dexscreener_first_seen")

    try:
        info = gmgn.get_token_info(token_address, chain)
        if isinstance(info, dict):
            pass
    except (ValueError, TypeError, KeyError, OSError):
        logger.debug("_detect_dev_insider_error", exc_info=True)
        logger.warning("get_token_info failed", exc_info=True)

    for trader in traders:
        wallet = trader["wallet"].lower()
        if wallet == deployer_lower:
            continue
        first_buy = trader.get("first_buy_at", 0)
        if not first_buy:
            continue
        total_bought = trader.get("total_bought_usd", 0)
        if total_bought < MIN_INSIDER_BUY_USD:
            continue
        lead_time_from_create = float("inf")
        if created_at and first_buy < created_at:
            lead_time_from_create = (created_at - first_buy) / 60
        trade_count = trader.get("trade_count", 0)

        signals: list[str] = []
        confidence = Confidence.LOW
        if created_at and first_buy < created_at:
            lead = (created_at - first_buy) / 60
            signals.append(f"Bought {lead:.0f}min before token creation (pre-launch access)")
            confidence = Confidence.CRITICAL if lead > 30 else Confidence.HIGH
        if trade_count <= 3 and total_bought >= 1000 and created_at:
            signals.append(f"Only {trade_count} trade(s), bought ${total_bought:+.0f} — low-float high-confidence")
            if confidence == Confidence.LOW:
                confidence = Confidence.MEDIUM
        if created_at and first_buy < created_at and confidence == Confidence.LOW:
            confidence = Confidence.MEDIUM
        if not signals:
            continue

        pnl = total_bought * AVG_PRE_TRENDING_PUMP
        results.append(
            InsiderEvidence(
                wallet=trader["wallet"],
                insider_type=InsiderType.DEV_INSIDER,
                bought_at=first_buy,
                bought_amount_usd=total_bought,
                event=f"Deployer {deployer[:8]} created token",
                event_at=created_at or 0,
                lead_time_minutes=lead_time_from_create if lead_time_from_create != float("inf") else 0,
                pnl_estimate=pnl,
                evidence="; ".join(signals[:2]),
                confidence=confidence,
            )
        )
    return results


async def _detect_cex_frontrun(
    token_address: str,
    chain: str,
    traders: list[dict[str, Any]],
    events: dict[str, Any],
) -> list[InsiderEvidence]:
    """Detect wallets that bought before a CEX listing."""
    results: list[InsiderEvidence] = []
    cex_listings = events.get("cex_listings", [])
    if not cex_listings:
        return results

    for listing in cex_listings:
        listing_at = listing.get("timestamp")
        if not listing_at:
            continue
        for trader in traders:
            first_buy = trader.get("first_buy_at", 0)
            if not first_buy or first_buy >= listing_at:
                continue
            lead_time = (listing_at - first_buy) / 60
            if lead_time < INSIDER_WINDOW_MIN or lead_time > PRE_CEX_WINDOW_MAX:
                continue
            total_bought = trader.get("total_bought_usd", 0)
            if total_bought < MIN_INSIDER_BUY_USD:
                continue
            known = label_wallet(trader["wallet"], chain)
            if known and known.get("confidence") == "HIGH":
                continue
            pnl = total_bought * CEX_LISTING_MULTIPLIER
            confidence = Confidence.CRITICAL if lead_time > 240 else Confidence.HIGH
            results.append(
                InsiderEvidence(
                    wallet=trader["wallet"],
                    insider_type=InsiderType.CEX_FRONTRUNNER,
                    bought_at=first_buy,
                    bought_amount_usd=total_bought,
                    event=f"CEX listing: {listing.get('symbol', '')} on {listing.get('detected', 'exchange')}",
                    event_at=listing_at,
                    lead_time_minutes=lead_time,
                    pnl_estimate=pnl,
                    evidence=f"Bought {total_bought:+.0f} USDT {lead_time:.0f}min before CEX listing",
                    confidence=confidence,
                )
            )
    return results


async def _detect_pumpfun_graduation(
    token_address: str,
    chain: str,
    traders: list[dict[str, Any]],
    events: dict[str, Any],
) -> list[InsiderEvidence]:
    """Detect wallets that bought a pump.fun token BEFORE it graduated to Raydium."""
    results: list[InsiderEvidence] = []
    if chain != "sol":
        return results
    graduated_at = events.get("pumpfun_graduated_at")
    if not graduated_at:
        return results

    for trader in traders:
        first_buy = trader.get("first_buy_at", 0)
        if not first_buy or first_buy >= graduated_at:
            continue
        lead_time = (graduated_at - first_buy) / 60
        if lead_time < INSIDER_WINDOW_MIN or lead_time > PRE_GRADUATION_WINDOW_MAX:
            continue
        total_bought = trader.get("total_bought_usd", 0)
        if total_bought < MIN_INSIDER_BUY_USD:
            continue
        pnl = total_bought * AVG_PUMPFUN_GRADUATION_PUMP
        confidence = Confidence.CRITICAL if lead_time > 120 else Confidence.HIGH
        results.append(
            InsiderEvidence(
                wallet=trader["wallet"],
                insider_type=InsiderType.PUMP_FUN_GRADUATION,
                bought_at=first_buy,
                bought_amount_usd=total_bought,
                event="pump.fun graduation to Raydium",
                event_at=graduated_at,
                lead_time_minutes=lead_time,
                pnl_estimate=pnl,
                evidence=(
                    f"Bought {total_bought:+.0f} USDT on pump.fun {lead_time:.0f}min before "
                    f"Raydium graduation, estimated ${pnl:,.0f} profit at peak"
                ),
                confidence=confidence,
            )
        )
    return results


async def _detect_snipers(
    token_address: str,
    chain: str,
    traders: list[dict[str, Any]],
    events: dict[str, Any],
    token_created_at: float | None,
) -> list[InsiderEvidence]:
    """Detect first-block snipers — wallets that bought in the first block."""
    results: list[InsiderEvidence] = []
    created_at = token_created_at or events.get("deployer_created_at")
    if not created_at:
        return results

    for trader in traders:
        first_buy = trader.get("first_buy_at", 0)
        if not first_buy:
            continue
        lead_time = first_buy - created_at
        if lead_time < 0:
            continue
        lead_minutes = lead_time / 60
        if lead_minutes > 0.5:
            continue
        total_bought = trader.get("total_bought_usd", 0)
        if total_bought < MIN_INSIDER_BUY_USD:
            continue
        pnl = total_bought * AVG_PRE_TRENDING_PUMP
        results.append(
            InsiderEvidence(
                wallet=trader["wallet"],
                insider_type=InsiderType.SNIPER,
                bought_at=first_buy,
                bought_amount_usd=total_bought,
                event=f"Token created at {_ts_human(created_at)}",
                event_at=created_at,
                lead_time_minutes=lead_minutes,
                pnl_estimate=pnl,
                evidence=f"First-block buy: {total_bought:+.0f} USDT within {lead_minutes * 60:.0f}s of token creation",
                confidence=Confidence.MEDIUM,
            )
        )
    return results
