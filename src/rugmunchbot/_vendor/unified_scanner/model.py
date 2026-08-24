# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/unified/model.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""ScanResult dataclass — data model for token scan results."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot._vendor.rag_pkg.intelligence import RAGContext

# RugBrain (rugbrain-api) base URL — shared by scanner.py and market_context.py.
# Lives here (the shared types module) to break the scanner <-> market_context
# import cycle: market_context imports these at module load, scanner imports
# them back from model.py instead of the reverse.
RUG_BRAIN_API_URL = os.getenv("RAG_API_URL", "http://rugbrain-api:8012").rstrip("/")
MARKET_CONTEXT_CACHE_TTL = int(os.getenv("MARKET_CONTEXT_CACHE_TTL", "300"))  # 5 min


# ── SENTINEL typed deep-scan output profiles ──────────────────────────
# These dataclasses are the structured output of the SENTINEL deep-forensic
# phase (see sentinel_pipeline / sentinel_phase.py).  Populated on the
# ScanResult via ``deep_profile`` when a deep-tier scan runs; left ``None``
# for quick-tier scans.


@dataclass
class WalletBreakdown:
    """Holder-wallet breakdown for the SENTINEL deep scan."""

    total_holders: int
    fresh_wallet_count: int  # wallets <7 days old
    fresh_wallet_pct: float
    exchange_funded_count: int  # funded from CEX hot wallets
    exchange_funded_pct: float
    instant_buy_count: int  # funded+bought within 60s
    instant_buy_pct: float
    exchange_breakdown: dict[str, int]  # {"binance": 187, "coinbase": 124}
    fake_diversification_score: float  # 0-100
    hhi: float = 0.0  # Herfindahl-Hirschman Index from holder_analysis (0-10000)
    hhi_risk_level: str = "unknown"  # "low" / "moderate" / "high" / "critical"


@dataclass
class BundleProfile:
    """Same-block bundle / sniper behaviour profile."""

    bundles_detected: int
    deployer_funded_wallets: int
    deployer_funded_pct: float
    same_block_snipers: int
    jito_bundles: int
    behavior_clusters: int
    cross_launch_repeat_wallets: int
    bundle_risk_score: int  # 0-100
    bundle_risk_level: str  # LOW/MEDIUM/HIGH/CRITICAL


@dataclass
class DeployerProfile:
    """Deployer wallet behaviour profile."""

    wallet_age_days: int
    total_deployments: int
    rug_rate: float  # pct of deployments that rugged
    funded_from: str  # "coinbase", "tornado", "unknown"
    chains_active: list[str]
    sleep_pattern: str  # "normal", "irregular", "bot-like"


@dataclass
class DeepScanProfile:
    """Aggregated SENTINEL deep-scan profile (quick scan = None)."""

    wallet_breakdown: WalletBreakdown | None = None
    bundle_profile: BundleProfile | None = None
    deployer_profile: DeployerProfile | None = None
    wash_trading_score: float = 0.0
    honeypot_risk: str = "unknown"  # "none", "possible", "detected"
    mev_exposure: str = "unknown"
    sentiment_score: float = 0.0
    social_velocity: str = "normal"
    pump_dump_detected: bool = False
    flash_loan_risk: bool = False
    oracle_manipulation_risk: bool = False


@dataclass
class ScanResult:
    """Single unified scan result."""

    token_address: str
    chain: str
    scanned_at: str = ""

    # Core identity
    name: str = ""
    symbol: str = ""
    price_usd: float = 0.0
    market_cap_usd: float = 0.0
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    age_hours: float = 0.0

    # Security
    safety_score: float = 50.0  # 0=scam, 100=safe
    confidence: float = 0.0  # 0-100, how much data we had
    risk_level: str = "unknown"
    risk_flags: list[str] = field(default_factory=list)
    green_flags: list[str] = field(default_factory=list)

    # Enrichment results (keyed by DataBus chain name)
    security: dict[str, Any] | None = None
    holders: dict[str, Any] | None = None
    holder_health: dict[str, Any] | None = None
    liquidity: dict[str, Any] | None = None
    volume_auth: dict[str, Any] | None = None
    dev_reputation: dict[str, Any] | None = None
    rug_patterns: dict[str, Any] | None = None
    premium: dict[str, Any] | None = None  # bundle/cluster/sniper/mev/wash/copy/insider
    smart_money: dict[str, Any] | None = None
    whale_alerts: dict[str, Any] | None = None
    cross_chain: dict[str, Any] | None = None
    launches: dict[str, Any] | None = None
    lite_chain_indexer_patterns: dict[str, Any] | None = None

    # Clever wallet analysis (own-data engines — funding_analysis.py)
    funding_analysis: dict[str, Any] | None = None  # exchange-funded / fresh / mixer holder %
    wallet_freshness: dict[str, Any] | None = None  # aggregate freshness of top holders
    holder_overlap: dict[str, Any] | None = None  # holder overlap vs known scam tokens

    # Token-level wallet freshness metrics (own-data engine — funding_analysis.py
    # ``compute_wallet_metrics``): fresh_wallet_pct / cex_funded_pct / first_tx_pct.
    wallet_metrics: dict[str, Any] | None = None

    # Own ClickHouse bundle-launch analysis (engines/own_bundle_detect.py
    # ``detect_bundled_launch``): coordinated sniper/bot buys at launch.
    bundle_analysis: dict[str, Any] | None = None

    # News-event-to-price correlation (own-data engine — event_correlation.py)
    event_correlation: dict[str, Any] | None = None  # which news events moved the price

    # News-based sentiment (engines/news_sentiment.py — RugBrain news_v2)
    news_sentiment: dict[str, Any] | None = None  # {sentiment, confidence, article_count, trending_score, ...}

    # Ground News-style news card (engines/news_sentiment.py — get_token_news_card)
    news_card: dict[str, Any] | None = None  # {sentiment, trending_score, articles, corroboration}

    # SENTINEL deep-forensic phase (Phase 5 — sentinel_phase.py).
    # Always populated with free-tier module results; premium module results
    # are gated behind elite/admin tier (or the x402 SENTINEL deep scan).
    sentinel: dict[str, Any] | None = None  # {free, premium, premium_unlocked, premium_preview, ...}

    # SENTINEL critical-finding boost (set by scoring.compute_score). When
    # two or more SENTINEL deep-forensic modules independently report
    # critical/high findings, an additional capped penalty is applied to the
    # safety score (0 = no boost, positive = extra risk).
    sentinel_boost: float = 0.0

    # SENTINEL typed deep-scan output (populated when a deep-tier scan runs;
    # None for quick-tier scans).  Structured profiles derived from the raw
    # ``sentinel`` dict above.
    deep_profile: DeepScanProfile | None = None

    # Market conditions that influenced this scan (Part 1 — market-aware
    # scanning). Populated by the scanner before scoring from RugBrain's
    # /api/v1/fear-index and /api/v1/market/mood endpoints (cached 5 min).
    market_context: MarketContext | None = None

    # Explainability (populated by scoring.compute_score)
    breakdown: dict[str, Any] | None = None  # per-dimension score breakdown
    evidence: dict[str, Any] | None = None  # raw findings backing the score

    # Metadata
    modules_run: list[str] = field(default_factory=list)
    enrichment_sources: list[str] = field(default_factory=list)
    tier: str = "free"
    scan_tier: str = "quick"  # "quick" | "deep" — which SENTINEL module set ran
    scan_duration_ms: float = 0.0
    rag_context: RAGContext | None = None  # RAG enrichment context (from RAGIntelligenceEnricher)

    # Background-phase status. The scanner returns a fast core+baseline result
    # while deep/intel/SENTINEL/news phases continue in a background task.
    # "complete" — every phase finished before the HTTP deadline;
    # "running"  — background phases still in flight (poll the status endpoint).
    background_status: str = "complete"

    # Result cache / stability metadata (score-stability work 2026-08-07).
    # ``cached`` is True when this result was served from the 1h Redis result
    # cache instead of a fresh scan; ``stability`` reports how many of the
    # recent scans for this token agree on the risk level
    # (``{"scans": N, "agreeing": M, "level": "medium", "agree_pct": 80}``).
    cached: bool = False
    stability: dict[str, Any] | None = None


@dataclass
class MarketContext:
    """Market conditions that influence token risk"""

    fear_index: float = 50.0  # 0-100 (0=fear, 100=greed)
    recent_exploits: int = 0  # exploits in last 24h
    chain_volume_24h: float = 0.0  # chain TVL change (percent)
    social_velocity: float = 0.0  # mention velocity for this token (0-100)
    deployer_freshness: str = "unknown"  # "new" / "established" / "unknown"
    source: str = "unknown"
    fetched_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "fear_index": self.fear_index,
            "recent_exploits": self.recent_exploits,
            "chain_volume_24h": self.chain_volume_24h,
            "social_velocity": self.social_velocity,
            "deployer_freshness": self.deployer_freshness,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> MarketContext | None:
        if not isinstance(data, dict):
            return None
        try:
            return cls(
                fear_index=float(data.get("fear_index", 50) or 50),
                recent_exploits=int(data.get("recent_exploits", 0) or 0),
                chain_volume_24h=float(data.get("chain_volume_24h", 0) or 0),
                social_velocity=float(data.get("social_velocity", 0) or 0),
                deployer_freshness=str(data.get("deployer_freshness", "unknown") or "unknown"),
                source=str(data.get("source", "unknown") or "unknown"),
                fetched_at=str(data.get("fetched_at", "") or ""),
            )
        except (TypeError, ValueError):
            return None
