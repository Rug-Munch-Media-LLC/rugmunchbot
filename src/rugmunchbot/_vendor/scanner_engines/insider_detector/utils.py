# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/insider_detector/utils.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Utility functions for insider detection (no circular deps with collectors)."""

from __future__ import annotations

from collections import defaultdict

from rugmunchbot._vendor.logging_shim import get_logger

from .config import AVG_PRE_TRENDING_PUMP, INSIDER_WINDOW_MIN
from .models import Confidence, InsiderEvidence, InsiderReport, InsiderType

logger = get_logger(__name__)


def _deduplicate_evidence(evidence_list: list[InsiderEvidence]) -> list[InsiderEvidence]:
    """Deduplicate by wallet + insider_type. Keep highest confidence and PnL."""
    best: dict[tuple[str, str], InsiderEvidence] = {}
    for ev in evidence_list:
        key = (ev.wallet.lower(), ev.insider_type)
        existing = best.get(key)
        if not existing:
            best[key] = ev
            continue
        confidence_score = {
            Confidence.CRITICAL: 4,
            Confidence.HIGH: 3,
            Confidence.MEDIUM: 2,
            Confidence.LOW: 1,
        }
        ev_score = confidence_score.get(ev.confidence, 0)
        ex_score = confidence_score.get(existing.confidence, 0)
        if ev_score > ex_score or (ev_score == ex_score and ev.pnl_estimate > existing.pnl_estimate):
            best[key] = ev
    return list(best.values())


def _generate_insights(report: InsiderReport) -> list[str]:
    """Generate human-readable insights from the report."""
    insights = []
    if not report.insiders:
        return insights

    types = defaultdict(list)
    for ev in report.insiders:
        types[ev.insider_type].append(ev)

    for insider_type, evs in types.items():
        wallets = {e.wallet for e in evs}
        total_pnl = sum(e.pnl_estimate for e in evs)
        avg_lead = sum(e.lead_time_minutes for e in evs) / len(evs) if evs else 0
        type_labels = {
            InsiderType.PRE_TRENDING: "pre-trending buyers",
            InsiderType.PRE_KOL: "pre-KOL buyers",
            InsiderType.DEV_INSIDER: "dev-connected wallets",
            InsiderType.CEX_FRONTRUNNER: "CEX frontrunners",
            InsiderType.PUMP_FUN_GRADUATION: "pre-graduation buyers",
            InsiderType.SNIPER: "first-block snipers",
        }
        label = type_labels.get(insider_type, insider_type)
        insights.append(f"{len(wallets)} {label}: avg lead {avg_lead:.0f}min, ${total_pnl:,.0f} estimated profit")

    for ev in report.insiders:
        if ev.confidence == Confidence.CRITICAL:
            insights.append(
                f"CRITICAL: {ev.wallet[:8]} {ev.insider_type} "
                f"({ev.lead_time_minutes:.0f}min lead, ${ev.pnl_estimate:,.0f} PnL)"
            )

    insights.append(
        f"Total: {len({e.wallet for e in report.insiders})} unique wallets, "
        f"${report.total_frontrun_value:,.0f} frontrun value"
    )
    return insights


async def _check_early_buy(
    token_address: str,
    chain: str,
    bought_at: float,
    bought_usd: float,
    tx_hash: str,
) -> tuple[bool, float, InsiderEvidence | None]:
    """Check if a specific buy was an early/insider buy.
    Uses lazy import to avoid circular dependency with collectors.
    """
    from .collectors import _gather_events

    try:
        events = await _gather_events(token_address, chain, None, None, None)
    except (ValueError, TypeError, KeyError, OSError):
        logger.debug("_check_early_buy_error", exc_info=True)
        logger.warning("_gather_events failed", exc_info=True)
        return False, 0, None

    event_timestamp = None
    event_desc = None
    for key in ("dexscreener_first_seen", "gmgn_first_trending", "first_kol_trade_at", "pumpfun_graduated_at"):
        ts = events.get(key)
        if ts and ts > bought_at and (event_timestamp is None or ts < event_timestamp):
            event_timestamp = ts
            event_desc = key

    if not event_timestamp:
        return False, 0, None

    lead_time = (event_timestamp - bought_at) / 60
    if lead_time < INSIDER_WINDOW_MIN:
        return False, 0, None

    pnl = bought_usd * AVG_PRE_TRENDING_PUMP
    evidence = InsiderEvidence(
        wallet="",
        insider_type=InsiderType.PRE_TRENDING,
        bought_at=bought_at,
        bought_amount_usd=bought_usd,
        event=event_desc or "Unknown event",
        event_at=event_timestamp,
        lead_time_minutes=lead_time,
        pnl_estimate=pnl,
        evidence=f"Bought ${bought_usd:,.0f} {lead_time:.0f}min before event",
        confidence=Confidence.HIGH if lead_time > 60 else Confidence.MEDIUM,
    )
    return True, lead_time, evidence
