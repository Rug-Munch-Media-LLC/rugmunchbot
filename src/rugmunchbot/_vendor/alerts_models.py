# VENDORED 2026-08-23 from rmi-backend/app/domains/alerts/models.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pydantic v2 models for the alerts domain.

No FastAPI imports. Pure data shapes.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class AlertType(StrEnum):
    """Valid alert trigger types. Matches the legacy alert_types enum."""

    LIQUIDITY_REMOVE = "liquidity_remove"
    MINT = "mint"
    BLACKLIST = "blacklist"
    HONEYPOT = "honeypot"
    RUG_PULL = "rug_pull"
    WHALE_MOVEMENT = "whale_movement"
    DEPLOYER_SELL = "deployer_sell"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class AlertSubscription(BaseModel):
    """A token alert subscription record (what we store in Redis)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(..., description="Unique subscription id, e.g. alert:1700000000")
    token_address: str = Field(..., min_length=1, max_length=256)
    alert_types: list[AlertType] = Field(
        default_factory=lambda: [AlertType.LIQUIDITY_REMOVE, AlertType.MINT, AlertType.BLACKLIST]
    )
    webhook_url: str | None = Field(default=None, max_length=2048)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    active: bool = True
    owner_id: str | None = Field(default=None, description="User id of subscription owner, None = anonymous")

    @field_validator("alert_types")
    @classmethod
    def _at_least_one_type(cls, v: list[AlertType]) -> list[AlertType]:
        if not v:
            raise ValueError("at least one alert_type required")
        return v


class CreateAlertRequest(BaseModel):
    """What the API accepts to create a subscription."""

    model_config = ConfigDict(str_strip_whitespace=True)

    token_address: str = Field(..., min_length=1, max_length=256)
    alert_types: list[AlertType] | None = None
    webhook_url: str | None = Field(default=None, max_length=2048)


class AlertEvent(BaseModel):
    """A fired alert, ready to broadcast to subscribers + WebSocket clients."""

    model_config = ConfigDict(use_enum_values=True)

    subscription_id: str
    token_address: str
    event_type: AlertType
    payload: dict[str, Any] = Field(default_factory=dict)
    fired_at: datetime = Field(default_factory=datetime.utcnow)
    severity: str = Field(default="info", description="info | warning | critical")


# ════════════════════════════════════════════════════════════════════════════
# PLATFORM ALERTS (canonical)
# ────────────────────────────────────────────────────────────────────────────
# Distinct concept from AlertSubscription/AlertEvent above (user watchlist
# alerts). PlatformAlert is the ONE shape every detector output is normalized
# into before routing to surfaces: site feed, Telegram channels, news.
# Detectors: bulletin / security-intel / scanner / launch / whale / syndicate /
# mempool. See app/domains/alerts/ingestion.py for the source adapters.
# ════════════════════════════════════════════════════════════════════════════


class PlatformAlertSeverity(StrEnum):
    """Canonical severity scale for platform alerts."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


class PlatformAlertCategory(StrEnum):
    """Canonical platform-alert categories (which detector produced it)."""

    RUG_RISK = "rug-risk"
    HONEYPOT = "honeypot"
    EXPLOIT = "exploit"
    WHALE = "whale"
    CEX_FLOW = "cex_flow"
    SYNDICATE = "syndicate"
    LAUNCH = "launch"
    MEMPOOL = "mempool"
    SCANNER = "scanner"
    BULLETIN = "bulletin"

    @classmethod
    def values(cls) -> list[str]:
        return [m.value for m in cls]


def _ts_bucket(ts: str, bucket_seconds: int = 300) -> str:
    """Bucket an ISO timestamp into a coarse window (default 5 minutes).

    Dedup keys compare alerts inside the same window, so a detector that
    re-emits the same event twice collapses into a single alert.
    """
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return str(int(parsed.timestamp()) // max(bucket_seconds, 1))
    except (ValueError, TypeError, OverflowError):
        return f"unparsed:{str(ts)[:16]}"


class PlatformAlert(BaseModel):
    """ONE canonical platform alert.

    Every detector is normalized into this shape by
    ``app.domains.alerts.ingestion.normalize_alert`` before routing.
    """

    model_config = ConfigDict(use_enum_values=True)

    id: str | None = None
    severity: PlatformAlertSeverity
    category: PlatformAlertCategory
    title: str = Field(..., min_length=1)
    body: str = ""
    token_address: str | None = None
    chain: str | None = None
    source: str
    event_key: str | None = Field(
        default=None,
        description="Deterministic dedup key (hash of category+entity+source+ts-bucket)",
    )
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    urls: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_event_key(self) -> PlatformAlert:
        if not self.event_key:
            self.event_key = self.derive_event_key()
        return self

    def derive_event_key(self, bucket_seconds: int = 300) -> str:
        """Deterministic dedup key: sha256(category|entity|source|ts-bucket)."""
        entity = (self.token_address or self.title or "").strip().lower()
        raw = f"{self.category}|{entity}|{self.source}|{_ts_bucket(self.ts, bucket_seconds)}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"evt:{digest}"

    def to_dict(self) -> dict[str, Any]:
        """Serializable dict for routing plans, transports, and persistence."""
        return self.model_dump(mode="json")
