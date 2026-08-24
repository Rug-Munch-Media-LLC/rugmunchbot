# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/_deployer_fingerprint_compute.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Fingerprint computation for deployer_fingerprint (degodding 2026-08-14).

Holds ``_compute_fingerprint`` — the orchestrator that runs the ClickHouse
queries (via ``_deployer_fingerprint_queries``), builds the per-token
outcomes, derives success rate / median lifespan / deployment velocity, and
assembles the scanned result. The risk-bucket / score / confidence math lives
in ``_deployer_fingerprint_helpers``; the funder profile enrichment lives in
``_deployer_fingerprint_profiles``.

``deployer_fingerprint.py`` re-exports ``_compute_fingerprint`` so the public
API and tests that patch ``deployer_fingerprint._compute_fingerprint`` keep
working. Imports are one-directional: this module never imports
``deployer_fingerprint``.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import run_sync, to_iso
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_helpers import (
    _assign_risk_bucket,
    _compute_risk_score,
    _confidence,
    _unavailable_result,
)
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_profiles import _build_funder_profile
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_queries import (
    _query_deployer_stats,
    _query_funder,
    _query_per_token_deployments,
    _query_token_dex_stats,
)

logger = get_logger(__name__)


async def _compute_fingerprint(deployer_address: str, chain_norm: str) -> dict[str, Any]:
    """Run all ClickHouse queries and assemble the fingerprint result."""
    now_naive = datetime.now(UTC).replace(tzinfo=None)

    # ── Round 1 (parallel, independent) ──
    stats_task = asyncio.create_task(run_sync(_query_deployer_stats, deployer_address, chain_norm))
    tokens_task = asyncio.create_task(run_sync(_query_per_token_deployments, deployer_address, chain_norm))
    funder_task = asyncio.create_task(run_sync(_query_funder, deployer_address, chain_norm))
    stats, tokens, funder = await asyncio.gather(stats_task, tokens_task, funder_task)

    if not stats or stats.get("tokens_deployed", 0) == 0:
        return _unavailable_result(deployer_address, chain_norm, reason="no deployer history")

    total_deployments = int(stats.get("tokens_deployed", 0) or 0)
    first_seen = stats.get("first_ts")
    last_seen = stats.get("last_ts")
    chains_used = stats.get("chains_used", [])

    # ── Round 2: per-token DEX survival (needs token list) ──
    dex_stats = await run_sync(_query_token_dex_stats, tokens, chain_norm)

    # ── Round 3: funder profile (needs funder address) ──
    funder_profile = await _build_funder_profile(funder, chain_norm, deployer_address)

    # ── Assemble token outcomes ──
    token_outcomes: list[dict[str, Any]] = []
    survived_count = 0
    matured_count = 0
    lifespans: list[float] = []
    evidence: list[dict[str, Any]] = []

    for tok in tokens:
        dex = dex_stats.get(tok["token_address"].lower()) or {}
        deployed_ts = tok.get("deployed_ts")
        first_dex_ts = dex.get("first_dex_ts")
        last_swap_ts = dex.get("last_swap_ts")
        swap_count = int(dex.get("swap_count", 0) or 0)

        # Lifespan: last swap minus the pair-created proxy (first DEX activity),
        # falling back to the deployment timestamp when no swaps exist.
        lifespan_hours: float | None = None
        if last_swap_ts is not None:
            base_ts = first_dex_ts or deployed_ts
            if base_ts is not None:
                try:
                    lifespan_hours = round(max((last_swap_ts - base_ts).total_seconds() / 3600, 0.0), 1)
                except (TypeError, ValueError):
                    lifespan_hours = None

        # Survived 7 days: had DEX activity at least 7 days after deployment
        survived_7d = False
        if deployed_ts is not None and last_swap_ts is not None:
            try:
                survived_7d = last_swap_ts >= deployed_ts + timedelta(days=7)
            except (TypeError, ValueError):
                survived_7d = False

        # Only tokens old enough to be judged count toward success rate
        matured = False
        if deployed_ts is not None:
            try:
                matured = deployed_ts + timedelta(days=7) <= now_naive
            except (TypeError, ValueError):
                matured = False

        if matured:
            matured_count += 1
            if survived_7d:
                survived_count += 1
        if lifespan_hours is not None:
            lifespans.append(lifespan_hours)

        token_outcomes.append(
            {
                "token_address": tok["token_address"],
                "chain": tok.get("chain") or chain_norm,
                "deployed_at": to_iso(tok.get("deployed_ts")),
                "deploy_tx": tok.get("deploy_tx"),
                "first_dex_ts": to_iso(first_dex_ts),
                "last_swap_ts": to_iso(last_swap_ts),
                "swap_count": swap_count,
                "lifespan_hours": lifespan_hours,
                "survived_7d": survived_7d,
                "matured": matured,
            }
        )

    success_rate: float | None = None
    if matured_count > 0:
        success_rate = round(survived_count / matured_count, 3)

    median_lifespan_hours: float | None = None
    if lifespans:
        lifespans_sorted = sorted(lifespans)
        n = len(lifespans_sorted)
        if n % 2 == 1:
            median_lifespan_hours = round(lifespans_sorted[n // 2], 1)
        else:
            median_lifespan_hours = round((lifespans_sorted[n // 2 - 1] + lifespans_sorted[n // 2]) / 2, 1)

    # Deployment velocity (deployments per week)
    deployments_per_week = 0.0
    if first_seen is not None and last_seen is not None and last_seen > first_seen:
        try:
            span_weeks = max((last_seen - first_seen).total_seconds() / (7 * 86400), 0.01)
            deployments_per_week = round(total_deployments / span_weeks, 3)
        except (TypeError, ValueError):
            deployments_per_week = 0.0

    # ── Risk bucket + score ──
    metrics: dict[str, Any] = {
        "total_deployments": total_deployments,
        "first_seen": first_seen,
        "survived_count": survived_count,
        "matured_count": matured_count,
        "success_rate": success_rate,
        "any_rug": any(not t["survived_7d"] for t in token_outcomes if t["matured"]),
        "funder_profile": funder_profile,
    }
    risk_bucket = _assign_risk_bucket(metrics, now_naive)
    risk_score = _compute_risk_score(risk_bucket, metrics)

    # Evidence: sample tokens (max 5) + funder proof
    for t in sorted(token_outcomes, key=lambda x: x["deployed_at"] or "", reverse=True)[:5]:
        evidence.append(
            {
                "kind": "token",
                "token_address": t["token_address"],
                "chain": t["chain"],
                "deployed_at": t["deployed_at"],
                "deploy_tx": t["deploy_tx"],
                "survived_7d": t["survived_7d"],
                "lifespan_hours": t["lifespan_hours"],
                "last_swap_ts": t["last_swap_ts"],
            }
        )
    if funder_profile.get("funder_address"):
        evidence.append(
            {
                "kind": "funder",
                "funder_address": funder_profile["funder_address"],
                "funding_tx": funder_profile.get("funding_tx"),
                "funding_ts": funder_profile.get("funding_ts"),
                "funding_token": funder_profile.get("funding_token"),
                "funder_is_cex": funder_profile.get("funder_is_cex"),
                "funder_is_fresh": funder_profile.get("funder_is_fresh"),
                "same_funder_multiple": funder_profile.get("same_funder_multiple"),
            }
        )

    return {
        "check": "deployer_fingerprint",
        "result": "scanned",
        "source": "own",
        "deployer": deployer_address,
        "chain": chain_norm or "any",
        "risk_bucket": risk_bucket,
        "risk_score": risk_score,
        "total_deployments": total_deployments,
        "chains_used": chains_used,
        "first_seen": to_iso(first_seen),
        "last_seen": to_iso(last_seen),
        "deployments_per_week": deployments_per_week,
        "survived_7d": survived_count,
        "matured_tokens": matured_count,
        "success_rate": success_rate,
        "median_lifespan_hours": median_lifespan_hours,
        "funder_profile": funder_profile,
        "evidence": evidence,
        "confidence": _confidence(total_deployments, matured_count, funder_profile),
    }
