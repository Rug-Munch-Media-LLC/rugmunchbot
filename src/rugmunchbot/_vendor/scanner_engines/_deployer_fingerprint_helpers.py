# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_deployer_fingerprint_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pure helpers for deployer_fingerprint (extracted from ``engines/deployer_fingerprint.py``).

Degodding split (2026-08-13): the risk-bucket assignment, risk-score
mapping, confidence computation, CEX-label classification, and the
graceful-degradation / error result builders are pure logic with no
ClickHouse or Redis access. They live here so ``deployer_fingerprint.py``
stays focused on the query + cache orchestration.

Every name is re-exported by ``deployer_fingerprint.py`` so
``deployer_fingerprint._assign_risk_bucket`` etc. keep resolving in tests
and callers (test_deployer_fingerprint imports them directly).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

# ── Risk buckets ──────────────────────────────────────────────────────────
RISK_BUCKETS = ("NEW", "REPEAT_CLEAN", "REPEAT_RUG", "FARM", "PROLIFIC")

# Known exchange keywords used to classify funder labels as CEX
_CEX_KEYWORDS = (
    "binance",
    "coinbase",
    "kraken",
    "okx",
    "okex",
    "bybit",
    "huobi",
    "gate.io",
    "gateio",
    "kucoin",
    "bitfinex",
    "mexc",
    "crypto.com",
    "bitget",
    "upbit",
    "bithumb",
    "gemini",
    "bitstamp",
    "coincheck",
    "bitpanda",
    "exchange",
    "cex",
)


def _assign_risk_bucket(metrics: dict[str, Any], now_naive: Any) -> str:
    """Assign the risk bucket per the C7 v1 spec.

    Precedence (operational ordering — see [REVIEW] note in the engine
    module docstring):
      1. NEW         — first deployment < 7 days ago
      2. PROLIFIC    — >50 deployments AND success_rate >= 0.8 (low rug rate)
      3. FARM        — >3 deployments funded by the same non-CEX wallet
      4. REPEAT_RUG  — any matured deployment had zero survival past 7 days
      5. REPEAT_CLEAN — all matured deployments survived 7+ days

    PROLIFIC is checked before REPEAT_RUG/FARM so a high-volume deployer with a
    genuinely low rug rate is not mislabeled as a serial rugger or farm; FARM is
    checked before REPEAT_RUG because the farm pattern (many deployments from one
    non-CEX wallet) is the more specific signal when it applies.
    """
    total = int(metrics.get("total_deployments", 0) or 0)
    first_seen = metrics.get("first_seen")

    # NEW: first deployment within the last 7 days
    if first_seen is not None:
        try:
            if now_naive - first_seen < timedelta(days=7):
                return "NEW"
        except (TypeError, ValueError):
            pass

    success_rate = metrics.get("success_rate")

    # PROLIFIC: >50 deployments with a confirmed low rug rate
    if total > 50 and success_rate is not None and success_rate >= 0.8:
        return "PROLIFIC"

    funder_profile = metrics.get("funder_profile", {})
    funder = funder_profile.get("funder_address")
    funder_is_cex = bool(funder_profile.get("funder_is_cex"))

    # FARM: >3 deployments funded by the same non-CEX wallet
    if total > 3 and funder and not funder_is_cex:
        return "FARM"

    # REPEAT_RUG: ANY matured deployment had zero survival past 7 days
    if metrics.get("any_rug"):
        return "REPEAT_RUG"

    # REPEAT_CLEAN: >1 deployments, all survived 7+ days
    # (A single old, clean deployment also lands here — see [REVIEW] note in
    # the module docstring: spec buckets do not define single-old-clean.)
    if total >= 1 and not metrics.get("any_rug"):
        return "REPEAT_CLEAN"

    return "NEW"


def _compute_risk_score(bucket: str, metrics: dict[str, Any]) -> int:
    """Map the risk bucket to a 0-100 risk score (higher = riskier)."""
    total = int(metrics.get("total_deployments", 0) or 0)
    survived = int(metrics.get("survived_count", 0) or 0)
    matured = int(metrics.get("matured_count", 0) or 0)
    funder_profile = metrics.get("funder_profile", {})

    if bucket == "NEW":
        return 50  # unknown, neutral
    if bucket == "REPEAT_CLEAN":
        return 15
    if bucket == "REPEAT_RUG":
        rug_count = max(matured - survived, 0)
        return min(95, 85 + min(rug_count, 5) * 2)
    if bucket == "FARM":
        score = 80
        if funder_profile.get("funder_is_fresh"):
            score += 10
        if funder_profile.get("same_funder_multiple"):
            score += 5
        return min(95, score)
    if bucket == "PROLIFIC":
        score = 25
        if matured > 0 and metrics.get("success_rate") is not None and metrics["success_rate"] >= 0.9:
            score -= 10
        return max(15, score)

    # Fallback for unknown bucket / no data
    return 50 if total == 0 else 30


def _confidence(total_deployments: int, matured_count: int, funder_profile: dict[str, Any]) -> float:
    """0.0-1.0 confidence based on data volume."""
    confidence = 0.3
    if total_deployments > 0:
        confidence = min(1.0, 0.4 + 0.1 * min(total_deployments, 5))
    if matured_count > 0:
        confidence = min(1.0, confidence + 0.15)
    if funder_profile.get("funder_address"):
        confidence = min(1.0, confidence + 0.1)
    return round(confidence, 2)


def _classify_cex(labels: list[dict[str, Any]]) -> tuple[bool, str | None]:
    """Return (is_cex, cex_label) from a list of wallet_labels rows."""
    for lbl in labels:
        category = (lbl.get("label_category") or "").lower()
        name = (lbl.get("label_name") or "").lower()
        if category in ("exchange", "cex") or any(kw in name for kw in _CEX_KEYWORDS):
            return True, lbl.get("label_name") or name
    return False, None


def _unavailable_result(
    deployer_address: str,
    chain_norm: str,
    reason: str = "clickhouse unavailable",
) -> dict[str, Any]:
    """Graceful degradation result — callers fall back to other engines."""
    return {
        "check": "deployer_fingerprint",
        "result": "unavailable",
        "source": "own",
        "deployer": deployer_address,
        "chain": chain_norm or "any",
        "risk_bucket": "NEW",
        "risk_score": 50,
        "total_deployments": 0,
        "success_rate": None,
        "median_lifespan_hours": None,
        "funder_profile": {},
        "evidence": [],
        "confidence": 0.0,
        "note": reason,
    }


def _error_result(message: str) -> dict[str, Any]:
    """Invalid-input error result."""
    return {
        "check": "deployer_fingerprint",
        "result": "error",
        "error": message,
        "risk_bucket": "NEW",
        "risk_score": 50,
        "total_deployments": 0,
        "success_rate": None,
        "median_lifespan_hours": None,
        "funder_profile": {},
        "evidence": [],
        "confidence": 0.0,
    }
