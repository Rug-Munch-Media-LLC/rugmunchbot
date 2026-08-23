# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/engines/insider_detector/models.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Data structures for insider detection reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


class InsiderType:
    """Type classification for insider detection methods."""

    PRE_TRENDING = "pre_trending_buyer"
    PRE_KOL = "pre_kol_buyer"
    DEV_INSIDER = "dev_insider"
    CEX_FRONTRUNNER = "cex_frontrunner"
    PUMP_FUN_GRADUATION = "pump_fun_graduate_frontrunner"
    SNIPER = "first_block_sniper"


class Confidence:
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass
class InsiderEvidence:
    """A single piece of insider evidence for one wallet-token pair."""

    wallet: str
    insider_type: str
    bought_at: float
    bought_amount_usd: float
    event: str
    event_at: float
    lead_time_minutes: float
    pnl_estimate: float
    evidence: str
    confidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "type": self.insider_type,
            "bought_at": self.bought_at,
            "bought_at_human": _ts_human(self.bought_at),
            "bought_amount_usd": round(self.bought_amount_usd, 2),
            "event": self.event,
            "event_at": self.event_at,
            "event_at_human": _ts_human(self.event_at),
            "lead_time_minutes": round(self.lead_time_minutes, 1),
            "pnl_estimate": round(self.pnl_estimate, 2),
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


@dataclass
class InsiderReport:
    """Complete insider detection report for one token."""

    token: str
    token_symbol: str
    chain: str
    deployer: str
    insiders: list[InsiderEvidence] = field(default_factory=list)
    total_frontrun_value: float = 0.0
    insights: list[str] = field(default_factory=list)

    def add(self, evidence: InsiderEvidence) -> None:
        self.insiders.append(evidence)
        self.total_frontrun_value += evidence.pnl_estimate

    def to_dict(self) -> dict[str, Any]:
        unique_wallets = {e.wallet for e in self.insiders}
        return {
            "token": self.token,
            "token_symbol": self.token_symbol,
            "chain": self.chain,
            "deployer": self.deployer,
            "insiders": sorted(
                [e.to_dict() for e in self.insiders],
                key=lambda x: x["pnl_estimate"],
                reverse=True,
            ),
            "summary": (
                f"{len(unique_wallets)} insider wallet{'s' if len(unique_wallets) != 1 else ''} "
                f"detected, ${self.total_frontrun_value:,.0f} total frontrun value"
            ),
            "total_insiders": len(self.insiders),
            "unique_wallets": len(unique_wallets),
            "total_frontrun_value": round(self.total_frontrun_value, 2),
            "insights": self.insights,
            "confidence_breakdown": {
                level: sum(1 for e in self.insiders if e.confidence == level)
                for level in [Confidence.CRITICAL, Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW]
            },
        }


def _ts_human(ts: float) -> str:
    """Format a Unix timestamp to a human-readable UTC string."""
    if not ts:
        return "unknown"
    try:
        return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return str(ts)
