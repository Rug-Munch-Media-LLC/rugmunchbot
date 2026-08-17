# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/_funding_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Pure funding-analysis helpers — funder classification + freshness scoring.

Extracted from ``engines/funding_analysis.py`` (degodding wave 5, 2026-08-08)
so the unit-testable "PURE LOGIC" section (funder classification, freshness
scoring, risk assessment, top-funder aggregation, scam-overlap aggregation)
lives beside the other pure engine helpers instead of inflating the
ClickHouse-driven analyzers.

Every function here is pure with respect to the query layer: inputs are
already-fetched rows / labels / percentages, outputs are plain dicts or
scalars. No ClickHouse or Redis access. Imports are one-directional:
``funding_analysis`` imports these helpers; the helpers never import
``funding_analysis``.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot._vendor.scanner_engines.known_entities import label_wallet

# ── Classification thresholds ─────────────────────────────────────────────
_FRESH_WALLET_MAX_TXS = 10  # funder with <=10 total txs => fresh wallet
_EXCHANGE_HIGH_PCT = 50.0  # exchange_funded_pct > 50 => coordinated launch
_FRESH_HIGH_PCT = 40.0  # fresh_wallet_pct > 40 => farm signal
_MIXER_HIGH_PCT = 5.0  # mixer_funded_pct > 5 => laundering signal
_OVERLAP_SCAM_PCT = 30.0  # holder overlap > 30% with known scam => red flag

# Freshness score weights (0-100, higher = fresher/more suspicious)
_AGE_WEIGHTS = ((0, 40), (7, 30), (30, 20), (90, 10))  # (max_age_days, pts)
_TX_WEIGHTS = ((10, 35), (25, 20), (50, 10))  # (max_txs, pts)
_CEX_FUNDED_PTS = 25

# Freshness classification bands (score -> class)
_FRESH_SUSPICIOUS_MIN = 70
_FRESH_CLEAN_MIN = 40

# ── Label classification keywords (match category/subtype/name, lowercased)
_CEX_KEYWORDS = (
    "cex",
    "exchange",
    "binance",
    "coinbase",
    "kraken",
    "okx",
    "okex",
    "bybit",
    "huobi",
    "kucoin",
    "gate.io",
    "gateio",
    "mexc",
    "bitget",
    "upbit",
    "bithumb",
    "gemini",
    "bitstamp",
    "crypto.com",
)
_MIXER_KEYWORDS = (
    "mixer",
    "tornado",
    "blender",
    "coinjoin",
    "wasabi",
    "privacy pool",
)

_RISK_DESCRIPTIONS = {
    "LOW": "Most holders were funded by independent wallets — no coordinated funding signal.",
    "MEDIUM": "A notable share of holders share funding sources — monitor for coordination.",
    "HIGH": "Majority of holders funded by exchange wallets — potential coordinated launch.",
}


def _classify_funder(
    funder: str,
    labels: list[dict[str, Any]],
    total_txs: int,
    chain: str,
) -> tuple[str, str | None]:
    """Classify a funder wallet.

    Returns ``(funding_type, label)`` where funding_type is one of
    ``exchange_funded`` / ``mixer_funded`` / ``fresh_wallet`` / ``other``.

    Priority:
      1. Static known_entities (CEX wallets, Tornado Cash, ...)
      2. wallet_labels category/subtype/name keywords
      3. Fresh wallet — funder with <= _FRESH_WALLET_MAX_TXS total txs
      4. other — unlabeled established wallet
    """
    # ── 1. Static known_entities (authoritative CEX/mixer addresses) ──
    static = label_wallet(funder, chain)
    if static and static.get("type", "").startswith("cex_"):
        return "exchange_funded", static.get("name") or None
    if static and static.get("type") == "mixer":
        return "mixer_funded", static.get("name") or None

    # ── 2. wallet_labels keywords ──
    text_parts: list[str] = []
    for lbl in labels:
        text_parts.append(" ".join(str(lbl.get(k) or "") for k in ("label_category", "label_subtype", "label_name")))
    text = " ".join(text_parts).lower()

    if any(kw in text for kw in _CEX_KEYWORDS):
        label = next((str(lbl.get("label_name") or "") for lbl in labels if lbl.get("label_name")), None)
        return "exchange_funded", label or "CEX"
    if any(kw in text for kw in _MIXER_KEYWORDS):
        label = next((str(lbl.get("label_name") or "") for lbl in labels if lbl.get("label_name")), None)
        return "mixer_funded", label or "mixer"

    # ── 3. Fresh wallet (<=10 total txs) ──
    if total_txs <= _FRESH_WALLET_MAX_TXS:
        return "fresh_wallet", None

    return "other", None


def _compute_freshness_score(age_days: float, total_txs: int, funded_by_cex: bool) -> int:
    """0-100 freshness score — higher = fresher/more suspicious.

    Weighting:
      - age:     0d => 40, <7d => 30, <30d => 20, <90d => 10, else 0
      - txs:     <=10 => 35, <=25 => 20, <=50 => 10, else 0
      - CEX funded: +25 (fresh wallet funded by CEX is a common farm pattern)
    """
    score = 0.0
    for max_age, pts in _AGE_WEIGHTS:
        if age_days <= max_age:
            score += pts
            break
    for max_txs, pts in _TX_WEIGHTS:
        if total_txs <= max_txs:
            score += pts
            break
    if funded_by_cex:
        score += _CEX_FUNDED_PTS
    return min(100, round(score))


def _classify_freshness(score: int) -> str:
    """Map a freshness score to FRESH_SUSPICIOUS / FRESH_CLEAN / ESTABLISHED."""
    if score >= _FRESH_SUSPICIOUS_MIN:
        return "FRESH_SUSPICIOUS"
    if score >= _FRESH_CLEAN_MIN:
        return "FRESH_CLEAN"
    return "ESTABLISHED"


def _assess_funding_risk(exchange_pct: float, fresh_pct: float, mixer_pct: float) -> str:
    """Risk assessment from funding-source percentages.

    HIGH: exchange-funded majority (potential coordinated launch) OR
          fresh-wallet farm majority.
    MEDIUM: elevated fresh-wallet or mixer share.
    LOW: otherwise.
    """
    if exchange_pct > _EXCHANGE_HIGH_PCT or fresh_pct > _FRESH_HIGH_PCT:
        return "HIGH"
    if fresh_pct > _FRESH_HIGH_PCT / 2 or mixer_pct > _MIXER_HIGH_PCT:
        return "MEDIUM"
    return "LOW"


def _build_top_funders(
    holder_details: list[dict[str, Any]],
    labels_map: dict[str, list[dict[str, Any]]],
    chain: str,
) -> list[dict[str, Any]]:
    """Aggregate holders per funder, return funders ranked by funded count.

    Only funders that funded >1 holder are surfaced (coordination signal).
    """
    funder_counts: dict[str, int] = {}
    for detail in holder_details:
        funder = detail.get("funder")
        if funder and detail.get("funding_type") in ("exchange_funded", "mixer_funded", "fresh_wallet", "other"):
            funder_counts[funder] = funder_counts.get(funder, 0) + 1

    top: list[dict[str, Any]] = []
    for funder, count in sorted(funder_counts.items(), key=lambda kv: kv[1], reverse=True):
        if count < 2:
            continue
        label = _funder_display_label(funder, labels_map.get(funder, []), chain)
        top.append({"address": funder, "label": label, "funded_holders": count})
    return top


def _funder_display_label(
    funder: str,
    labels: list[dict[str, Any]],
    chain: str,
) -> str | None:
    """Best display label for a funder (static known_entities first, then wallet_labels)."""
    static = label_wallet(funder, chain)
    if static and static.get("confidence") == "HIGH":
        return static.get("name") or None
    for lbl in labels:
        name = str(lbl.get("label_name") or "").strip()
        if name:
            return name
    return None


def _aggregate_scam_overlap(token_address: str, chain: str, comparisons: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize the worst per-token overlap comparison."""
    if not comparisons:
        return {
            "check": "scam_overlap",
            "result": "unavailable",
            "source": "own",
            "token_address": token_address,
            "chain": chain,
            "scam_tokens_checked": 0,
            "max_overlap_pct": 0.0,
            "worst_token": None,
            "worst_token_is_scam": False,
            "risk_assessment": "LOW",
            "comparisons": [],
            "note": "overlap unavailable for all known scam tokens",
        }

    worst = max(comparisons, key=lambda c: float(c.get("overlap_pct") or 0.0))
    max_overlap = float(worst.get("overlap_pct") or 0.0)
    worst_is_scam = bool(worst.get("token_b_is_scam"))
    risk = (
        "HIGH"
        if (worst_is_scam and max_overlap > _OVERLAP_SCAM_PCT)
        else ("MEDIUM" if max_overlap > _OVERLAP_SCAM_PCT else "LOW")
    )

    return {
        "check": "scam_overlap",
        "result": "scanned",
        "source": "own",
        "token_address": token_address,
        "chain": chain,
        "scam_tokens_checked": len(comparisons),
        "max_overlap_pct": max_overlap,
        "worst_token": worst.get("token_b"),
        "worst_token_is_scam": worst_is_scam,
        "risk_assessment": risk,
        "comparisons": comparisons,
        "note": "worst holder overlap vs known scam tokens (deployer_history is_scam_related=1)",
    }
