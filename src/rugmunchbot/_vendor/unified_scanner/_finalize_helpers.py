# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/unified/_finalize_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Finalize helpers — post-phase tail for ``UnifiedTokenScanner``.

Extracted from ``unified/scanner.py`` (degodding wave 8, 2026-08-09) so the
post-phase finalize block (scoring recompute, news enrichment, RAG context,
persist, publish, Telegram alert, deployer auto-label) lives beside the news
enrichment runners instead of inflating the scanner orchestration class.

All helpers are best-effort: each enrichment degrades gracefully when an
external backend is unavailable, so a failing helper never fails the scan.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import TYPE_CHECKING, Any

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.unified_scanner.persistence import (
    auto_label_deployer,
    persist_result,
    publish_rag_finding,
)
from rugmunchbot._vendor.unified_scanner.scoring import compute_score

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from rugmunchbot._vendor.unified_scanner.model import ScanResult

logger = get_logger(__name__)

# ── RAG enrichment budget (embedding fail-fast) ──────────────────────────
# RAG enrichment (semantic scam-similarity search) is best-effort. When the
# embedding service (Infinity /embeddings) is slow or down, a single
# retrieval can hang for the embedder's full retry budget (60s httpx timeout
# x 3 attempts per collection, x 9 collections). This module never lets that
# stall the scan: enrichment is hard-capped at ``RAG_ENRICH_BUDGET_S`` and,
# after a timeout, is skipped entirely for ``RAG_ENRICH_COOLDOWN_S`` so
# repeat scans during an outage fail fast instead of re-paying the budget.
RAG_ENRICH_BUDGET_S = float(os.getenv("RAG_ENRICH_BUDGET_S", "6.0"))
RAG_ENRICH_COOLDOWN_S = float(os.getenv("RAG_ENRICH_COOLDOWN_S", "60"))
# Monotonic timestamp of the last enrichment timeout (0 = healthy).
_RAG_ENRICH_LAST_FAILURE: float = 0.0


async def _enrich_scan_result_bounded(result: ScanResult) -> Any:
    """Best-effort RAG enrichment with a hard time budget + outage latch.

    Returns the ``RAGContext`` (or ``None`` when RAG is unavailable).
    Guarantees:
      - never blocks longer than ``RAG_ENRICH_BUDGET_S``
      - after a timeout, skips enrichment entirely for
        ``RAG_ENRICH_COOLDOWN_S`` (the embedder is down — do not re-pay
        the budget on every scan)
      - never raises: the scan result is returned unchanged on any failure

    Only *timeouts* trip the cooldown latch — an embedder that fails fast
    (e.g. connection refused) already degrades quickly and should not
    suppress later recoveries.
    """
    global _RAG_ENRICH_LAST_FAILURE
    if time.monotonic() - _RAG_ENRICH_LAST_FAILURE < RAG_ENRICH_COOLDOWN_S:
        logger.debug(
            "rag_enrichment_skipped_cooldown token=%s",
            result.token_address[:16],
        )
        return None
    try:
        from rugmunchbot._vendor.rag_pkg.intelligence import RAGIntelligenceEnricher

        enricher = RAGIntelligenceEnricher()
        rag_context = await asyncio.wait_for(
            enricher.enrich_scan_result(result),
            timeout=RAG_ENRICH_BUDGET_S,
        )
        # Success — clear the outage latch so a recovered embedder is used.
        _RAG_ENRICH_LAST_FAILURE = 0.0
        return rag_context
    except TimeoutError:
        _RAG_ENRICH_LAST_FAILURE = time.monotonic()
        logger.warning(
            "rag_enrichment_timed_out budget_s=%.1f token=%s",
            RAG_ENRICH_BUDGET_S,
            result.token_address[:16],
        )
        return None
    except Exception:
        logger.debug("RAG enrichment unavailable", exc_info=True)
        return None


def _reset_rag_enrich_latch() -> None:
    """Clear the RAG-outage cooldown latch (for tests)."""
    global _RAG_ENRICH_LAST_FAILURE
    _RAG_ENRICH_LAST_FAILURE = 0.0


async def run_news_sentiment(r: ScanResult, address: str, chain: str) -> None:
    """Analyze news sentiment for the scanned token (best-effort).

    Queries RugBrain RAG ``news_v2`` for articles mentioning the token,
    computes BULLISH/BEARISH/NEUTRAL/NO_DATA sentiment plus a 0-100 media
    attention (trending) score, and stores the result on
    ``ScanResult.news_sentiment``. Adds the ``HIGH_MEDIA_ATTENTION``
    green flag when trending_score > 70.

    Runs AFTER ``compute_score`` so the appended flag is not overwritten
    by scoring's ``green_flags`` assignment. Time-boxed so a slow RAG
    backend can never stall the scan. Never raises — news sentiment is
    best-effort enrichment, not a scan blocker.
    """
    try:
        from rugmunchbot._vendor.scanner_engines.news_sentiment import analyze_token_news_sentiment

        sentiment = await asyncio.wait_for(
            analyze_token_news_sentiment(
                token_symbol=r.symbol or "",
                token_name=r.name or "",
                chain=chain,
            ),
            timeout=10,
        )
        if not isinstance(sentiment, dict):
            return
        r.news_sentiment = sentiment
        r.modules_run.append("news_sentiment")
        if sentiment.get("result") == "scanned" and int(sentiment.get("article_count", 0) or 0) > 0:
            r.enrichment_sources.append("news_sentiment:rag")
        if int(sentiment.get("trending_score", 0) or 0) > 70:
            flag = "HIGH_MEDIA_ATTENTION"
            if flag not in r.green_flags:
                r.green_flags.append(flag)
    except Exception as exc:
        logger.debug("news_sentiment_failed", error=str(exc)[:120])


async def get_news_card(r: ScanResult, address: str, chain: str) -> None:
    """Build the Ground News-style news card for the scanned token.

    Queries RugBrain RAG ``news_v2`` and rates each article across
    factuality / source reputation / market impact / sentiment axes,
    then stores the card on ``ScanResult.news_card``. On-chain
    corroboration is passed through from ``event_correlation`` when the
    scanner has its own on-chain evidence of the news event.

    Runs AFTER ``run_news_sentiment`` so both news enrichment fields
    are available to Telegram/report rendering. Time-boxed so a slow
    RAG backend can never stall the scan. Never raises — the news card
    is best-effort enrichment, not a scan blocker.
    """
    try:
        from rugmunchbot._vendor.scanner_engines.news_sentiment import get_token_news_card

        on_chain: bool | None = None
        if isinstance(r.event_correlation, dict) and r.event_correlation.get("events"):
            on_chain = True

        card = await asyncio.wait_for(
            get_token_news_card(
                token_symbol=r.symbol or "",
                token_name=r.name or "",
                chain=chain,
                limit=5,
                on_chain_evidence=on_chain,
            ),
            timeout=10,
        )
        if not isinstance(card, dict):
            return
        r.news_card = card
        r.modules_run.append("news_card")
        if card.get("result") == "scanned" and card.get("articles"):
            r.enrichment_sources.append("news_card:rag")
    except Exception as exc:
        logger.debug("news_card_failed", error=str(exc)[:120])


async def finalize_background(
    result: ScanResult,
    address: str,
    chain: str,
    status: Callable[..., Awaitable[None]],
    fire_alert: Callable[[ScanResult], asyncio.Task[Any] | None],
) -> None:
    """Finalize a completed background scan: score, enrich, persist, publish.

    Runs the post-phase tail of ``_run_background_phases``:
      1. recompute the score with every module's output,
      2. run news sentiment + news card enrichment,
      3. apply news/market risk adjustments (second pass, keeps flags in sync),
      4. enrich with RAG context,
      5. record own-data coverage metrics,
      6. persist (Postgres + Redis cache, fire-and-forget),
      7. publish high-risk findings to the RAG pipeline + event bus,
      8. push the Telegram alert (bounded shield) and auto-label deployers.

    Best-effort throughout: a failing sub-step is logged and never raises.
    ``status`` is the caller's progress publisher; ``fire_alert`` schedules
    the Telegram alert and returns the background task (or ``None`` when
    gated off).
    """
    # ── Score (recomputed with all modules) ──
    scoring = compute_score(result)
    result.breakdown = scoring["breakdown"]
    result.evidence = scoring["evidence"]

    # ── News sentiment (free enrichment, after scoring so the
    #    HIGH_MEDIA_ATTENTION flag survives compute_score) ──
    await run_news_sentiment(result, address, chain)

    # ── Ground News-style news card (free enrichment) ──
    await get_news_card(result, address, chain)

    # ── News-driven risk adjustment (post-scoring — news engines
    #    run after compute_score, so the bonus/penalty is applied
    #    in a second pass that keeps score/flags/breakdown in sync) ──
    try:
        from rugmunchbot._vendor.unified_scanner.scoring import apply_news_scoring

        apply_news_scoring(result)
    except Exception as exc:
        logger.debug("news_scoring_failed", error=str(exc)[:120])

    # ── Market-aware risk adjustment (post-scoring — market context
    #    is fetched before the phases, then applied here so the
    #    score/flags/breakdown stay in sync) ──
    try:
        from rugmunchbot._vendor.unified_scanner.scoring import apply_market_scoring

        apply_market_scoring(result)
    except Exception as exc:
        logger.debug("market_scoring_failed", error=str(exc)[:120])

    # ── Enrich with RAG context (similar patterns from previous scans) ──
    # Hard-bounded + fail-fast: a hanging embedder must never stall the
    # scan finalize. On timeout/failure the scan keeps its deterministic
    # result and rag_context degrades to None (skip-with-warning).
    result.rag_context = await _enrich_scan_result_bounded(result)

    # ── Record own-data coverage metrics ──
    try:
        from app.domains.scanner.metrics import record_token_scan_coverage

        modules_run_list = [{"module": m, "status": "success", "result": {}} for m in result.modules_run]
        record_token_scan_coverage(result.enrichment_sources, modules_run_list)
    except ImportError:
        pass
    except Exception:
        pass

    # ── Persist: Postgres + Redis cache (fire-and-forget) ──
    asyncio.create_task(persist_result(result))

    # ── Publish high-risk findings to RAG pipeline ──
    if result.risk_level in ("high", "critical"):
        try:
            from app.core.events import ScanCompleted, get_event_bus

            bus = get_event_bus()
            await bus.publish(
                ScanCompleted(
                    token_address=result.token_address,
                    chain=result.chain,
                    safety_score=result.safety_score,
                    risk_level=result.risk_level,
                    risk_flags=result.risk_flags,
                    modules_run=result.modules_run,
                )
            )
        except Exception as e:
            logger.debug("scan_event_publish_failed", error=str(e)[:80])

        try:
            await publish_rag_finding(result)
        except Exception as e:
            logger.debug("rag_finding_publish_failed", error=str(e)[:80])

    # ── Push Telegram alert (fire-and-forget — never block the scan) ──
    alert_task = fire_alert(result)
    try:
        # Bounded, shielded grace period: lets the alert complete while
        # the background phase is still winding down. On timeout the
        # alert continues independently — it never delays the HTTP
        # response past the 25s budget.
        if alert_task is not None:
            await asyncio.wait_for(asyncio.shield(alert_task), timeout=5)
    except TimeoutError:
        pass

    # ── Auto-label high-risk deployer wallets (fire-and-forget) ──
    try:
        asyncio.create_task(auto_label_deployer(result))
    except Exception as exc:
        logger.debug("deployer_label_schedule_failed", error=str(exc)[:80])
