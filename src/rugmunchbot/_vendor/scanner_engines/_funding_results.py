# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/_funding_results.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Graceful-degradation result builders for funding_analysis (Batch 13).

Every ``*_unavailable`` / ``*_error`` dict factory from
``funding_analysis.py`` lives here. Pure functions — no I/O, no cache
state. ``funding_analysis.py`` re-exports each name so existing imports
and ``fa._*_result`` references in tests keep working.
"""

from __future__ import annotations

from typing import Any


def _unavailable_result(token_address: str, chain_norm: str, reason: str = "clickhouse unavailable") -> dict[str, Any]:
    """Graceful degradation result (Metric 1)."""
    return {
        "check": "funding_sources",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain_norm,
        "holders_analyzed": 0,
        "exchange_funded_pct": 0.0,
        "fresh_wallet_pct": 0.0,
        "mixer_funded_pct": 0.0,
        "unknown_pct": 0.0,
        "top_funders": [],
        "holder_details": [],
        "risk_assessment": "LOW",
        "note": reason,
    }


def _error_result(token_address: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result (Metric 1)."""
    return {
        "check": "funding_sources",
        "result": "error",
        "error": message,
        "token_address": token_address,
        "chain": chain_norm,
        "holders_analyzed": 0,
        "exchange_funded_pct": 0.0,
        "fresh_wallet_pct": 0.0,
        "mixer_funded_pct": 0.0,
        "unknown_pct": 0.0,
        "top_funders": [],
        "holder_details": [],
        "risk_assessment": "LOW",
    }


def _freshness_unavailable(address: str, chain_norm: str, reason: str = "clickhouse unavailable") -> dict[str, Any]:
    """Graceful degradation result (Metric 2)."""
    return {
        "check": "wallet_freshness",
        "result": "unavailable",
        "source": "own",
        "address": address,
        "chain": chain_norm,
        "age_days": 0.0,
        "total_txs": 0,
        "funded_by_cex": False,
        "first_funder": None,
        "first_funder_label": None,
        "first_seen_at": None,
        "freshness_score": 0,
        "classification": "ESTABLISHED",
        "note": reason,
    }


def _overlap_unavailable(
    token_a: str, token_b: str, chain_norm: str, reason: str = "clickhouse unavailable"
) -> dict[str, Any]:
    """Graceful degradation result (Metric 3)."""
    return {
        "check": "holder_overlap",
        "result": "unavailable",
        "source": "own",
        "token_a": token_a,
        "token_b": token_b,
        "chain": chain_norm,
        "holders_a": 0,
        "holders_b": 0,
        "common_holders": 0,
        "overlap_pct": 0.0,
        "token_b_is_scam": False,
        "risk_assessment": "LOW",
        "note": reason,
    }


def _overlap_error(token_a: str, token_b: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result (Metric 3)."""
    return {
        "check": "holder_overlap",
        "result": "error",
        "error": message,
        "token_a": token_a,
        "token_b": token_b,
        "chain": chain_norm,
        "holders_a": 0,
        "holders_b": 0,
        "common_holders": 0,
        "overlap_pct": 0.0,
        "token_b_is_scam": False,
        "risk_assessment": "LOW",
    }


def _scam_overlap_error(token_address: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result (scanner helper)."""
    return {
        "check": "scam_overlap",
        "result": "error",
        "error": message,
        "token_address": token_address,
        "chain": chain_norm,
        "scam_tokens_checked": 0,
        "max_overlap_pct": 0.0,
        "worst_token": None,
        "worst_token_is_scam": False,
        "risk_assessment": "LOW",
        "comparisons": [],
    }


def _wallet_metrics_unavailable(
    token_address: str,
    chain_norm: str,
    reason: str = "clickhouse unavailable",
) -> dict[str, Any]:
    """Graceful degradation result (Metric 4)."""
    return {
        "check": "wallet_metrics",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain_norm,
        "holders_analyzed": 0,
        "fresh_wallet_pct": 0.0,
        "cex_funded_pct": 0.0,
        "first_tx_pct": 0.0,
        "fresh_wallet_count": 0,
        "cex_funded_count": 0,
        "first_tx_count": 0,
        "holder_details": [],
        "note": reason,
    }


def _wallet_metrics_error(token_address: str, chain_norm: str, message: str) -> dict[str, Any]:
    """Invalid-input error result (Metric 4)."""
    return {
        "check": "wallet_metrics",
        "result": "error",
        "error": message,
        "token_address": token_address,
        "chain": chain_norm,
        "holders_analyzed": 0,
        "fresh_wallet_pct": 0.0,
        "cex_funded_pct": 0.0,
        "first_tx_pct": 0.0,
        "fresh_wallet_count": 0,
        "cex_funded_count": 0,
        "first_tx_count": 0,
        "holder_details": [],
    }
