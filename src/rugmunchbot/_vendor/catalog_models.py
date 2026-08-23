# VENDORED 2026-08-23 from rmi-backend/app/catalog/models.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""T27A - Canonical entity models for the RMI data catalog.

Pydantic v2 (per ADR-0002). Every entity is persisted in exactly one primary
store, with cross-store references (string IDs) to related entities in other
stores. CatalogService resolves these references transparently.

Cross-store ID conventions:
  Token, Wallet, Contract:  f"{chain.value}:{address}"
  Entity, Alert, NewsItem, RAGFinding, ScanReport: UUID4 hex
  Qdrant point_id:         16-byte UUID hex (matches RAG engine)

Persistence (per v4.0 §T27 "Why each store exists"):
  Redis:        hot token data, rate limits, alert state, cron locks
  Postgres:     users, api_keys, subscriptions, x402 receipts, audit
                alerts, news_items, scan_reports
  Neo4j:        entities, wallets, deployers, contracts, entity labels
                (graph traversal, Cypher)
  Qdrant:       RAG embeddings, token similarity, news embeddings
  MinIO:        news raw HTML, report markdown, RAG source docs
  Filesystem:   ingest tmp, log rotation (transient)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


# ── Enums ───────────────────────────────────────────────────────────
class Chain(StrEnum):
    """All chains the platform indexes. Per v4.0 §T27."""

    SOLANA = "solana"
    ETHEREUM = "ethereum"
    BASE = "base"
    ARBITRUM = "arbitrum"
    OPTIMISM = "optimism"
    POLYGON = "polygon"
    BSC = "bsc"
    TRON = "tron"
    BITCOIN = "bitcoin"
    AVALANCHE = "avalanche"
    FANTOM = "fantom"
    GNOSIS = "gnosis"
    # EVM subnets (sample - full 96 in CHAIN_REGISTRY)
    SEPOLIA = "sepolia"
    LINEA = "linea"
    SCROLL = "scroll"
    ZKSYNC = "zksync"
    BLAST = "blast"
    MANTLE = "mantle"


# Full chain registry - v4.0 says 96 chains. We track the canonical 20 here
# plus extend via CHAIN_REGISTRY for the remaining 76. Adding a chain is a
# one-line edit.
CHAIN_REGISTRY: dict[str, dict[str, str]] = {
    "solana": {"name": "Solana", "type": "svm", "native": "SOL", "explorer": "https://solscan.io"},
    "ethereum": {
        "name": "Ethereum",
        "type": "evm",
        "native": "ETH",
        "explorer": "https://etherscan.io",
    },
    "base": {"name": "Base", "type": "evm", "native": "ETH", "explorer": "https://basescan.org"},
    "arbitrum": {
        "name": "Arbitrum One",
        "type": "evm",
        "native": "ETH",
        "explorer": "https://arbiscan.io",
    },
    "optimism": {
        "name": "Optimism",
        "type": "evm",
        "native": "ETH",
        "explorer": "https://optimistic.etherscan.io",
    },
    "polygon": {
        "name": "Polygon",
        "type": "evm",
        "native": "MATIC",
        "explorer": "https://polygonscan.com",
    },
    "bsc": {
        "name": "BNB Smart Chain",
        "type": "evm",
        "native": "BNB",
        "explorer": "https://bscscan.com",
    },
    "tron": {"name": "TRON", "type": "tvm", "native": "TRX", "explorer": "https://tronscan.org"},
    "bitcoin": {
        "name": "Bitcoin",
        "type": "utxo",
        "native": "BTC",
        "explorer": "https://mempool.space",
    },
    "avalanche": {
        "name": "Avalanche C-Chain",
        "type": "evm",
        "native": "AVAX",
        "explorer": "https://snowtrace.io",
    },
    "fantom": {
        "name": "Fantom Opera",
        "type": "evm",
        "native": "FTM",
        "explorer": "https://ftmscan.com",
    },
    "gnosis": {
        "name": "Gnosis Chain",
        "type": "evm",
        "native": "xDAI",
        "explorer": "https://gnosisscan.io",
    },
}


class RiskTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Entities (Neo4j primary) ───────────────────────────────────────
class Entity(BaseModel):
    """A logical entity resolved across chains. Neo4j primary."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(..., description="UUID, Neo4j primary key")
    label: str | None = None
    aliases: list[str] = Field(default_factory=list)
    first_seen: datetime
    last_seen: datetime
    risk_score: int | None = Field(None, ge=0, le=100)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    store: Literal["neo4j"] = "neo4j"


class EntityLabel(BaseModel):
    """A label attached to an entity. Neo4j primary."""

    model_config = ConfigDict(extra="ignore")

    entity_id: str
    label: str
    source: Literal["manual", "heuristic", "third_party"]
    confidence: float = Field(ge=0.0, le=1.0)
    added_at: datetime
    added_by: str
    store: Literal["neo4j"] = "neo4j"


# ── Wallets + Deployers (Neo4j primary) ───────────────────────────
class Wallet(BaseModel):
    """An on-chain wallet. Neo4j primary; linked to Entity."""

    model_config = ConfigDict(extra="ignore")

    wallet_id: str = Field(..., description='"chain:address", e.g. "solana:7Np41..."')
    chain: Chain
    address: str
    entity_id: str | None = None
    first_seen: datetime
    last_seen: datetime
    tx_count: int = 0
    total_volume_usd: float = 0.0
    is_deployer: bool = False
    is_known_exchange: bool = False
    is_suspicious: bool = False
    reputation_score: int | None = Field(None, ge=0, le=100)
    store: Literal["neo4j"] = "neo4j"

    @field_validator("wallet_id")
    @classmethod
    def _check_id_format(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError("wallet_id must be 'chain:address'")
        chain, _ = v.split(":", 1)
        if chain not in {c.value for c in Chain}:
            # Allow unknown chains (forward compat) but flag them
            pass
        return v


class Deployer(Wallet):
    """A wallet that has deployed at least one token. Extends Wallet."""

    model_config = ConfigDict(extra="ignore")

    deployments: list[str] = Field(default_factory=list, description="token_ids")
    rug_count: int = 0
    legit_count: int = 0
    avg_token_lifetime_days: float = 0.0
    reputation_score: int | None = Field(
        None,
        ge=0,
        le=100,
        description="Weighted: legit_count * 1.0 - rug_count * 3.0 + age_bonus - news_penalty. Cached in Redis TTL 1h.",
    )


# ── Tokens (Postgres primary) ──────────────────────────────────────
class Token(BaseModel):
    """A token contract. Postgres primary; references Deployer in Neo4j."""

    model_config = ConfigDict(extra="ignore")

    token_id: str = Field(..., description='"chain:address"')
    chain: Chain
    address: str
    symbol: str
    name: str
    decimals: int
    deployer_wallet_id: str | None = Field(None, description="Cross-store ref to Wallet (Neo4j)")
    deployed_at: datetime
    initial_supply: int
    current_supply: int | None = None
    is_honeypot: bool | None = None
    is_mintable: bool | None = None
    is_proxy: bool | None = None
    tax_buy_bps: int | None = None
    tax_sell_bps: int | None = None
    risk_tier: RiskTier | None = None
    risk_score: int | None = Field(None, ge=0, le=100)
    risk_factors: list[str] = Field(default_factory=list)
    rag_embedding_id: str | None = Field(None, description="Cross-store ref to Qdrant point (16-byte hex)")
    store: Literal["postgres"] = "postgres"


# ── Alerts (Postgres primary) ──────────────────────────────────────
class Alert(BaseModel):
    """A risk alert. Postgres primary."""

    model_config = ConfigDict(extra="ignore")

    alert_id: str = Field(..., description="UUID")
    token_id: str | None = None
    wallet_id: str | None = None
    chain: Chain | None = None
    alert_type: Literal[
        "rug_detected",
        "deployer_history",
        "liquidity_drain",
        "honeypot_detected",
        "high_tax_change",
        "whale_dump",
    ]
    severity: Literal["info", "warning", "critical"]
    title: str
    description: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    resolved_at: datetime | None = None
    store: Literal["postgres"] = "postgres"


# ── News (Postgres primary + Qdrant embeddings) ────────────────────
class NewsItem(BaseModel):
    """A news article from RSS. Postgres primary; embeddings in Qdrant."""

    model_config = ConfigDict(extra="ignore")

    news_id: str = Field(..., description="UUID")
    url: HttpUrl
    title: str
    summary: str
    body_markdown: str | None = None
    image_url: str | None = None
    source: str
    published_at: datetime
    ingested_at: datetime
    chains_mentioned: list[Chain] = Field(default_factory=list)
    tokens_mentioned: list[str] = Field(default_factory=list)
    wallets_mentioned: list[str] = Field(default_factory=list)
    sentiment_score: float | None = Field(None, ge=-1.0, le=1.0)
    bias: str | None = None
    sentiment_label: str | None = None
    factual_rating: str | None = None
    source_tier: int | None = None
    rating_confidence: float | None = None
    ai_analysis: str | None = None
    rag_embedding_id: str | None = None
    store: Literal["postgres"] = "postgres"


# ── RAG Findings (Qdrant primary + Postgres metadata) ───────────────
class RAGFinding(BaseModel):
    """A fact extracted by RAG. Qdrant primary (vector); metadata in Postgres."""

    model_config = ConfigDict(extra="ignore")

    finding_id: str = Field(..., description="UUID")
    source_type: Literal["news", "onchain", "audit", "social", "manual"]
    source_url: HttpUrl | None = None
    source_token_id: str | None = None
    source_wallet_id: str | None = None
    claim: str
    confidence: float = Field(ge=0.0, le=1.0)
    extracted_at: datetime
    qdrant_point_id: str
    store: Literal["qdrant"] = "qdrant"


# ── Reports (Postgres + MinIO) ──────────────────────────────────────
class ScanReport(BaseModel):
    """A research report. Postgres primary; markdown in MinIO."""

    model_config = ConfigDict(extra="ignore")

    report_id: str = Field(..., description="UUID")
    subject_type: Literal["token", "wallet", "deployer"]
    subject_id: str
    generated_at: datetime
    generated_by_model: str
    risk_score: int = Field(ge=0, le=100)
    risk_tier: RiskTier
    sections: dict[str, str] = Field(default_factory=dict)
    markdown_url: HttpUrl | None = None
    paid_via_x402: str | None = None
    store: Literal["postgres", "minio"] = "postgres"

    def to_markdown(self) -> str:
        """Render report sections to a single Markdown document."""
        parts = [
            f"# Research Report: {self.subject_type.title()} `{self.subject_id}`",
            "",
            f"**Generated:** {self.generated_at.isoformat()}",
            f"**Generated by:** {self.generated_by_model}",
            f"**Risk score:** {self.risk_score}/100 ({self.risk_tier.value.upper()})",
            "",
        ]
        for section, body in self.sections.items():
            parts.append(f"## {section.replace('_', ' ').title()}")
            parts.append("")
            parts.append(body)
            parts.append("")
        parts.append("---")
        parts.append(f"*Report ID: {self.report_id}*")
        return "\n".join(parts)


# ── Wire-format helpers ─────────────────────────────────────────────
def utcnow() -> datetime:
    """Timezone-aware UTC now. Pydantic serializes to ISO 8601."""
    return datetime.now(UTC)


# ── RAG engine collections (kept here so catalog + RAG share the list) ─
COLLECTIONS: list[str] = [
    # Per v4.0 catalog/RAG bridge - these are the canonical 13 RAG
    # collections that also have Token/Wallet/etc cross-refs.
    "scam_intel",
    "deployer_history",
    "wallet_labels",
    "contract_audit",
    "phishing_db",
    "defi_hacks",
    "rug_timeline",
    "vuln_patterns",
    "crime_reports",
    "transaction_patterns",
    "known_scams",
    "token_analysis",
    "market_intel",
]
