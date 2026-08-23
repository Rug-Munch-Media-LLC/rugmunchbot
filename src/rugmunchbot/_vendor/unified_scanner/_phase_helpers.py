# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/unified/_phase_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Phase helpers — background phase sub-runners for ``UnifiedTokenScanner``.

Extracted from ``unified/scanner.py`` (degodding wave 7, 2026-08-09) so the
two largest phase blocks (``_run_wallet_analysis`` C-20 and its freshness
aggregator ``_gather_freshness`` C-17) live beside the other phase helpers
instead of inflating the scanner orchestration class.

Both are best-effort: each engine degrades gracefully when own ClickHouse
data is unavailable, so a failed sub-engine never fails the scan. Errors are
captured (and logged) inside ``run_wallet_analysis``; ``gather_freshness``
returns an ``unavailable`` summary when no wallet finished a scan.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.logging_shim import get_logger

if TYPE_CHECKING:
    from rugmunchbot._vendor.unified_scanner.model import ScanResult

logger = get_logger(__name__)


async def gather_freshness(tasks: list[Any]) -> dict[str, Any]:
    """Aggregate per-wallet freshness results into a token-level summary.

    Runs the given freshness tasks concurrently and returns a summary dict
    with the fresh-wallet concentration signal (``fresh_wallet_pct`` is the
    share of analyzed top holders classified FRESH_SUSPICIOUS).
    """
    results = await asyncio.gather(*tasks, return_exceptions=True)
    analyzed: list[dict[str, Any]] = []
    for res in results:
        if not isinstance(res, BaseException) and isinstance(res, dict) and res.get("result") == "scanned":
            analyzed.append(res)

    if not analyzed:
        return {"result": "unavailable", "reason": "no freshness data", "analyzed": 0}

    classifications = [r.get("classification", "ESTABLISHED") for r in analyzed]
    suspicious = sum(1 for c in classifications if c == "FRESH_SUSPICIOUS")
    clean = sum(1 for c in classifications if c == "FRESH_CLEAN")
    established = sum(1 for c in classifications if c == "ESTABLISHED")
    avg_score = sum(float(r.get("freshness_score", 0) or 0) for r in analyzed) / len(analyzed)

    return {
        "result": "scanned",
        "analyzed": len(analyzed),
        "fresh_suspicious": suspicious,
        "fresh_clean": clean,
        "established": established,
        "fresh_wallet_pct": round(suspicious / len(analyzed) * 100, 1) if analyzed else 0.0,
        "avg_freshness_score": round(avg_score, 1),
        "wallets": [
            {
                "address": r.get("address"),
                "freshness_score": r.get("freshness_score"),
                "classification": r.get("classification"),
                "age_days": r.get("age_days"),
                "total_txs": r.get("total_txs"),
                "funded_by_cex": r.get("funded_by_cex"),
            }
            for r in analyzed
        ],
    }


async def run_wallet_analysis(r: ScanResult, address: str, chain: str) -> None:
    """Run exchange-funding, freshness, and scam-overlap analysis.

    Best-effort: each engine degrades gracefully when own ClickHouse data
    is unavailable, so a failed sub-engine never fails the scan.
    """
    try:
        from rugmunchbot._vendor.scanner_engines.funding_analysis import (
            analyze_funding_sources,
            analyze_scam_overlap,
            compute_wallet_freshness,
            compute_wallet_metrics,
        )

        holders: list[str] = []
        holder_data = r.holders if isinstance(r.holders, dict) else None
        if holder_data:
            top = holder_data.get("top_holders") or []
            if isinstance(top, list):
                holders = [str(h.get("address", "")) for h in top if isinstance(h, dict) and h.get("address")]
            elif isinstance(top, dict):
                # Some providers return {address: pct}
                holders = [str(a) for a in top if a]

        # ── Metric 1: Exchange-funded wallet % ──
        funding_task = analyze_funding_sources(address, chain, holders=holders or None)

        # ── Metric 2: Freshness of the top few holders (bounded) ──
        freshness_holders = holders[:10]
        freshness_tasks = [compute_wallet_freshness(h, chain) for h in freshness_holders]
        freshness_task = gather_freshness(freshness_tasks)

        # ── Metric 3: Holder overlap vs known scam tokens ──
        overlap_task = analyze_scam_overlap(address, chain)

        # ── Metric 4: Token-level wallet freshness metrics (any token) ──
        metrics_task = compute_wallet_metrics(address, chain, holders=holders or None)

        funding: Any
        freshness: Any
        overlap: Any
        metrics: Any
        funding, freshness, overlap, metrics = await asyncio.gather(
            funding_task,
            freshness_task,
            overlap_task,
            metrics_task,
            return_exceptions=True,
        )

        if not isinstance(funding, BaseException) and isinstance(funding, dict):
            r.funding_analysis = funding
        if not isinstance(freshness, BaseException) and isinstance(freshness, dict):
            r.wallet_freshness = freshness
        if not isinstance(overlap, BaseException) and isinstance(overlap, dict):
            r.holder_overlap = overlap
        if not isinstance(metrics, BaseException) and isinstance(metrics, dict):
            r.wallet_metrics = metrics

        r.modules_run.extend(["funding_analysis", "wallet_freshness", "holder_overlap", "wallet_metrics"])
        r.enrichment_sources.append("wallet_analysis:own")
    except Exception as exc:
        logger.debug("wallet_analysis_failed", error=str(exc)[:120])


async def run_bundle_analysis(r: ScanResult, address: str, chain: str) -> None:
    """Run own ClickHouse bundle-launch detection (best-effort).

    Detects coordinated sniper/bot buys in the first blocks after deployment
    (see ``engines/own_bundle_detect.detect_bundled_launch``), plus same-slot
    buy concentration on Solana (``detect_same_slot_bundler``, Helius-backed;
    degrades to a ``[REVIEW]`` note when Helius is unreachable), and stores
    the result on ``ScanResult.bundle_analysis`` so scoring can emit
    ``BUNDLE_DETECTED``. Never raises — a missing engine or ClickHouse
    outage degrades to ``unavailable``.
    """
    try:
        from rugmunchbot._vendor.scanner_engines.own_bundle_detect import (
            detect_bundled_launch,
            detect_same_slot_bundler,
        )

        bundle_task = asyncio.create_task(detect_bundled_launch(address, chain))
        same_slot_task = asyncio.create_task(detect_same_slot_bundler(address, chain))
        gathered = await asyncio.gather(bundle_task, same_slot_task, return_exceptions=True)
        bundle: Any = gathered[0]
        same_slot: Any = gathered[1]

        if isinstance(bundle, dict):
            r.bundle_analysis = bundle
            r.modules_run.append("bundle_analysis")
            if bundle.get("result") == "scanned":
                r.enrichment_sources.append("bundle_analysis:own")
        if isinstance(same_slot, dict) and isinstance(r.bundle_analysis, dict):
            r.bundle_analysis["same_slot"] = same_slot
    except Exception as exc:
        logger.debug("bundle_analysis_failed", error=str(exc)[:120])
