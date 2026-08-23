# VENDORED 2026-08-23 from rmi-backend/app/domains/scanner/unified/persistence.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Persistence — Postgres save, Redis cache, RAG publishing, deployer labeling."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from rugmunchbot._vendor.logging_shim import get_logger

if TYPE_CHECKING:
    from rugmunchbot._vendor.unified_scanner.model import ScanResult

logger = get_logger(__name__)

# Stability window — how many recent risk levels we track per token (Redis).
STABILITY_MAX_SCANS = 10
STABILITY_TTL = 4 * 3600  # 4h — long enough to see repeated scans drift


def _stability_key(chain: str, address: str) -> str:
    return f"scanner:stability:{chain}:{address}"


def _to_persist_dict(result: ScanResult) -> dict[str, Any]:
    """Convert ScanResult to a plain dict suitable for JSON storage."""
    return {
        "token_address": result.token_address,
        "chain": result.chain,
        "scanned_at": result.scanned_at,
        "name": result.name,
        "symbol": result.symbol,
        "price_usd": result.price_usd,
        "market_cap_usd": result.market_cap_usd,
        "liquidity_usd": result.liquidity_usd,
        "volume_24h_usd": result.volume_24h_usd,
        "age_hours": result.age_hours,
        "safety_score": result.safety_score,
        "confidence": result.confidence,
        "risk_level": result.risk_level,
        "risk_flags": result.risk_flags,
        "green_flags": result.green_flags,
        "tier": result.tier,
        "lite_chain_indexer_patterns": result.lite_chain_indexer_patterns,
        "funding_analysis": result.funding_analysis,
        "wallet_freshness": result.wallet_freshness,
        "holder_overlap": result.holder_overlap,
        "wallet_metrics": result.wallet_metrics,
        "bundle_analysis": result.bundle_analysis,
        "scan_duration_ms": result.scan_duration_ms,
        "modules_run": result.modules_run,
        "enrichment_sources": result.enrichment_sources,
        "event_correlation": result.event_correlation,
        "sentinel": result.sentinel,
    }


async def persist_result(result: ScanResult) -> None:
    """Persist scan result to Postgres + Redis (fire-and-forget).

    1. Save ScanResult ORM row to ``scan_results`` table
    2. Best-effort upsert token into ``tokens`` table if new
    3. Cache in Redis for 1 hour
    4. All errors are logged but never raised
    """
    # ── 1. Save scan_result row ──
    try:
        from app.models.scan import ScanResult as ScanResultORM

        session = await _get_async_session()
        if session is None:
            return
        async with session:
            raw_json = json.dumps(_to_persist_dict(result), default=str)

            scan_orm = ScanResultORM(
                id=uuid.uuid4(),
                token_address=result.token_address,
                chain=result.chain,
                symbol=result.symbol[:64] if result.symbol else "",
                name=result.name[:256] if result.name else "",
                safety_score=round(result.safety_score),
                risk_score=round(100 - result.safety_score),
                risk_level=result.risk_level,
                risk_flags=result.risk_flags[:50],
                warnings=result.green_flags[:50],
                modules_run=result.modules_run[:50],
                extra={"raw": raw_json},
                scanned_at=datetime.fromisoformat(result.scanned_at) if result.scanned_at else datetime.now(UTC),
                tier_required=result.tier,
                confidence=round(result.confidence),
            )
            session.add(scan_orm)

            # ── 2. Best-effort token upsert ──
            await _maybe_upsert_token(session, result)

            await session.commit()
            logger.info(
                "scan_persisted",
                chain=result.chain,
                address=result.token_address,
                scan_id=str(scan_orm.id),
            )

    except (SQLAlchemyError, ConnectionError, OSError, TimeoutError):
        logger.exception(
            "scan_persist_failed",
            chain=result.chain,
            address=result.token_address,
        )

    # ── 3. Redis cache (independent of Postgres) — full legacy dict so a
    #    cache hit can be served as a complete ScanResult ──
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async
        from rugmunchbot._vendor.unified_scanner._legacy import _to_legacy_dict

        r = get_redis_async()
        if r is not None:
            cache_key = f"sentinelscan:{result.chain}:{result.token_address}"
            cache_val = json.dumps(_to_legacy_dict(result), default=str)
            await r.setex(cache_key, 3600, cache_val)
            logger.debug("scan_redis_cached", key=cache_key)
    except (RedisError, ConnectionError, OSError, TimeoutError, ImportError):
        logger.exception("scan_redis_cache_failed")

    # ── 4. Stability tracking (best-effort) — record the risk level exactly
    #    once per completed scan run (the shared-result flag prevents the same
    #    run being counted twice when both persists fire). ──
    if result.background_status == "complete" and not getattr(result, "_stability_recorded", False):
        try:
            from rugmunchbot._vendor.redis_client import get_redis_async

            r = get_redis_async()
            if r is not None:
                await _record_stability(r, result.chain, result.token_address, result.risk_level)
            result._stability_recorded = True  # type: ignore[attr-defined]  # dynamic guard flag
        except (RedisError, ConnectionError, OSError, TimeoutError):
            logger.debug("scan_stability_record_failed")


async def _record_stability(r: Any, chain: str, address: str, risk_level: str) -> None:
    """Append this scan's risk level to the token's recent-history window."""
    if not risk_level:
        return
    key = _stability_key(chain, address)
    try:
        raw = await r.get(key)
        levels = json.loads(raw) if raw else []
        if not isinstance(levels, list):
            levels = []
        levels = [str(x) for x in levels if isinstance(x, str)]
        levels.append(risk_level)
        levels = levels[-STABILITY_MAX_SCANS:]
        await r.setex(key, STABILITY_TTL, json.dumps(levels))
    except (RedisError, TypeError, ValueError):
        logger.debug("scan_stability_write_failed")


async def read_stability(chain: str, address: str) -> dict[str, Any] | None:
    """Read the stability summary for a token: how many recent scans agree.

    Returns ``{"scans": N, "agreeing": M, "level": "medium", "agree_pct": 80}``
    or ``None`` when there is no recorded history. ``level`` is the most
    frequent recent risk level and ``agreeing`` counts scans matching it.
    """
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is None:
            return None
        raw = await r.get(_stability_key(chain, address))
        if not raw:
            return None
        levels = json.loads(raw)
        if not isinstance(levels, list) or not levels:
            return None
        levels = [str(x) for x in levels if isinstance(x, str)]
        if not levels:
            return None
        from collections import Counter

        counter = Counter(levels)
        level, agreeing = counter.most_common(1)[0]
        return {
            "scans": len(levels),
            "agreeing": agreeing,
            "level": level,
            "agree_pct": round(agreeing / len(levels) * 100),
        }
    except (RedisError, TypeError, ValueError, ConnectionError, OSError):
        return None


async def _maybe_upsert_token(session: Any, result: ScanResult) -> None:
    """Best-effort upsert of discovered token into the ``tokens`` table."""
    if not result.symbol:
        return
    try:
        from sqlalchemy import select

        from app.models.token import Token

        stmt = select(Token).where(
            Token.chain == result.chain,
            Token.address == result.token_address,
        )
        tok_result = await session.execute(stmt)
        existing_token = tok_result.scalar_one_or_none()

        if existing_token is None:
            token_orm = Token(
                address=result.token_address,
                chain=result.chain,
                name=result.name[:256] if result.name else "",
                symbol=result.symbol[:64] if result.symbol else "",
                decimals=9,
                total_supply=0.0,
                circulating_supply=0.0,
                holders=0,
                verified=False,
                risk_score=round(100 - result.safety_score),
                risk_level=result.risk_level,
                extra={"first_scanned_at": result.scanned_at},
            )
            session.add(token_orm)
            logger.info(
                "token_created_from_scan",
                chain=result.chain,
                address=result.token_address,
                symbol=result.symbol,
            )
    except (SQLAlchemyError, ConnectionError, OSError, TimeoutError, ValueError):
        logger.warning(
            "token_upsert_skipped",
            chain=result.chain,
            address=result.token_address,
            detail="Token ORM model may not match DB schema; upsert skipped",
        )


async def _get_async_session() -> Any | None:
    """Get an async SQLAlchemy session, falling back to direct connection if needed."""
    from app.core.database import async_session_factory, engine

    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return async_session_factory()
    except (ConnectionRefusedError, OSError, ConnectionError):
        logger.debug("scan_persist_primary_engine_unreachable")
    except (SQLAlchemyError, RuntimeError, TimeoutError):
        logger.debug("scan_persist_primary_engine_error")

    import os

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    host = os.getenv("POSTGRES_HOST") or os.getenv("DB_HOST") or "rmi-postgres"
    user = os.getenv("POSTGRES_USER", "rmi")
    password = os.getenv("POSTGRES_PASSWORD", "rmi")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "rmi")
    fallback_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"

    try:
        fallback_engine = create_async_engine(fallback_url, pool_size=2, max_overflow=2)
        fallback_factory = async_sessionmaker(
            fallback_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        logger.info("scan_persist_fallback_engine", host=host, db=db)
        return fallback_factory()
    except (SQLAlchemyError, ImportError, OSError, ConnectionError, TimeoutError) as exc:
        logger.warning("scan_persist_fallback_failed", host=host, error=str(exc)[:100])
        return None


async def publish_rag_finding(result: ScanResult) -> None:
    """Publish high-risk scan finding to rmi-rag via Redis channel ``rmi:rag:ingest``."""
    from rugmunchbot._vendor.redis_client import get_redis_async

    r = get_redis_async()
    if r is None:
        logger.warning("Redis unavailable — skipping RAG finding publish")
        return

    # Dedup: don't republish the same token within 24h
    dedup_key = f"rag:dedup:{result.chain}:{result.token_address}"
    if await r.exists(dedup_key):
        logger.debug(
            "rag_finding_duplicate_skipped",
            chain=result.chain,
            token=result.token_address,
        )
        return
    await r.setex(dedup_key, 86400, "1")

    payload = {
        "token_address": result.token_address,
        "chain": result.chain,
        "name": result.name,
        "symbol": result.symbol,
        "safety_score": result.safety_score,
        "confidence": result.confidence,
        "risk_level": result.risk_level,
        "risk_flags": result.risk_flags[:20],
        "green_flags": result.green_flags[:10],
        "price_usd": result.price_usd,
        "liquidity_usd": result.liquidity_usd,
        "age_hours": result.age_hours,
        "scanned_at": result.scanned_at,
        "modules_run": result.modules_run[:30],
    }

    payload_json = json.dumps(payload)

    await r.publish("rmi:rag:ingest", payload_json)
    await r.lpush("rmi:rag:findings_queue", payload_json)

    logger.info(
        "rag_finding_published",
        token=result.token_address,
        chain=result.chain,
        risk=result.risk_level,
    )


async def auto_label_deployer(result: ScanResult) -> None:
    """Auto-label the deployer wallet when dev_reputation shows high risk."""
    dev = result.dev_reputation
    if not dev or not isinstance(dev, dict):
        return

    deployer_addr = dev.get("deployer_address") or dev.get("address", "")
    risk_score = float(dev.get("risk_score", 0) or 0)
    risk_label = str(dev.get("risk_label", dev.get("reputation_label", "UNKNOWN")))

    if risk_score < 70 or not deployer_addr or deployer_addr == "unknown":
        return

    from app.domains.scanner.shared.deployer_labeler import auto_label_deployer as _auto_label

    await _auto_label(
        deployer_address=deployer_addr,
        chain=result.chain,
        risk_score=risk_score,
        risk_label=risk_label,
        token_address=result.token_address,
        token_name=result.name,
        token_symbol=result.symbol,
        safety_score=result.safety_score,
        risk_flags=result.risk_flags,
        source="unified_token_scanner",
    )
