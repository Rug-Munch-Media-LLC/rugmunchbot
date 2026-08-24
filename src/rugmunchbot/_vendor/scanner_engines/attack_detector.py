# VENDORED 2026-08-24 from rmi-backend/app/domains/scanner/engines/attack_detector.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
C6 — Time-Based Attack Detection Engine
==========================================
Pure ClickHouse-powered detection of sandwich attacks, pump & dumps, and
flash loans built PURELY from our own `wallet_memory.dex_events` (59M+ rows).
No external APIs.

Detectors (all read only our own dex_events):
  1. Sandwich      — attacker buy + victim swap + attacker sell in the SAME
                     block on the SAME pair, with the same sender on the
                     front-run and back-run legs.
  2. Pump & dump   — 4h volume > 10x trailing-24h baseline, price spike
                     > 100% from baseline, then drawdown > 50% from peak
                     within 24h (per-pair, from reserve-ratio price series).
  3. Flash loan    — v1 pattern-based: same tx_hash with >= 4 swap events
                     where a single pair's total in ≈ total out (repay) and
                     the target swap sits in between.

The three detectors + their shared window-query scaffolding live in
``engines/_attack_helpers.py`` (extracted degodding wave 5, 2026-08-08).
This module is the orchestrator: window caching, dedupe, persistence,
Redis / Telegram alert wiring, and whale marking.

Wiring (after detection):
  - Persist events to `wallet_memory.attack_events` (MergeTree)
  - Push `attack_alert:{chain}:{token}` to Redis for real-time consumers
  - Send Telegram scanner alert when confidence == HIGH
  - Mark attacker wallets as whale in dex_events (ALTER TABLE UPDATE)

Scheduling:
  - Run as a periodic job every 5 minutes scanning the last hour of blocks
    (see run_periodic_detection / scripts/run_attack_detection.py).
  - Every ClickHouse query runs with `max_execution_time = 30`.
  - Results cached in Redis (TTL 15m) to avoid re-scanning the same blocks.

Usage:
    from rugmunchbot._vendor.scanner_engines.attack_detector import run_attack_detection

    result = await run_attack_detection(chain="all", hours_back=1, notify=True)
    # {
    #   "result": "scanned",
    #   "chains_scanned": ["ethereum", ...],
    #   "events": [...],
    #   "events_new": 3,
    #   "inserted": 3,
    #   "alerts_pushed": 2,
    #   "telegram_sent": 1,
    #   "whale_marked": 1,
    #   ...
    # }
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from redis.exceptions import RedisError

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._attack_helpers import (
    _CH_DB,
    _RESULT_CACHE,
    _RESULT_CACHE_MAX,
    CACHE_TTL_SECONDS,
    DEX_EVENTS_TABLE,
    MAX_EXECUTION_TIME,
    _normalize_chain,
    _to_float,
    detect_flash_loans,
    detect_pump_dumps,
    detect_sandwiches,
)
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from rugmunchbot._vendor.scanner_engines.own_dex_data.format import _normalize_amount
from rugmunchbot._vendor.scanner_engines.own_dex_data.query import get_token_price_simple

logger = get_logger("attack_detector")

# ── Constants ────────────────────────────────────────────────────────────────
ATTACK_TABLE = f"{_CH_DB}.attack_events"

ALL_CHAINS: tuple[str, ...] = (
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "optimism",
    "base",
    "avalanche",
    "fantom",
    "gnosis",
    "solana",
)


# ── Lazy clients ─────────────────────────────────────────────────────────────
_ch_client: Any = None
_redis_client: Any = None

# In-process USD price cache (per-detector result cache lives in _attack_helpers)
_USD_PRICE_CACHE: dict[str, float] = {}
_USD_PRICE_CACHE_MAX = 200


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client for the wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=35,
        )
        _ch_client.execute("SELECT 1")
        logger.info("Attack detector ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Attack detector ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


async def _get_redis() -> Any | None:
    """Lazy-init async Redis client. Returns None if unavailable."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        redis_url = os.getenv("REDIS_URL", "")
        if redis_url and "://" in redis_url:
            url = redis_url
        else:
            r_host = os.getenv("REDIS_HOST", "rmi-redis")
            r_port = os.getenv("REDIS_PORT", "6379")
            r_db = os.getenv("REDIS_DB", "0")
            r_pass = os.getenv("REDIS_PASSWORD", "")
            url = f"redis://:{r_pass}@{r_host}:{r_port}/{r_db}" if r_pass else f"redis://{r_host}:{r_port}/{r_db}"
        _redis_client = aioredis.from_url(url, decode_responses=True)
        await _redis_client.ping()
        logger.info("Attack detector Redis connected: %s", url.split("@")[-1])
        return _redis_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Attack detector Redis unavailable: %s", e)
        _redis_client = None
        return None


# ── Schema ────────────────────────────────────────────────────────────────────
ATTACK_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {ATTACK_TABLE} (
    attack_type LowCardinality(String),  -- sandwich, pump_dump, flash_loan
    confidence LowCardinality(String),   -- HIGH, MEDIUM, LOW
    chain LowCardinality(String),
    block_number UInt64,
    tx_hashes Array(String),
    token_address String,
    pair_address String,
    value_usd Nullable(Float64),
    detected_at DateTime DEFAULT now()
) ENGINE = MergeTree()
ORDER BY (chain, block_number, attack_type)
"""

_ATTACK_INSERT_COLS = (
    "attack_type",
    "confidence",
    "chain",
    "block_number",
    "tx_hashes",
    "token_address",
    "pair_address",
    "value_usd",
    "detected_at",
)


def _ensure_attack_table() -> bool:
    """Create wallet_memory.attack_events if it does not exist yet."""
    client = _get_ch_client()
    if client is None:
        return False
    try:
        client.execute(ATTACK_TABLE_DDL, settings={"max_execution_time": MAX_EXECUTION_TIME})
        return True
    except (ValueError, RuntimeError, OSError) as exc:
        logger.warning("attack_events table ensure failed: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Orchestrator — run all detectors, dedupe, persist, wire alerts
# ═══════════════════════════════════════════════════════════════════════════


async def run_attack_detection(
    chain: str = "all",
    hours_back: int = 1,
    notify: bool = True,
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Run all three attack detectors and wire results into the alert pipeline.

    Steps:
      1. Check the Redis window cache (TTL 15m) — skip already-scanned blocks.
      2. Run sandwich / pump_dump / flash_loan detectors against dex_events.
      3. Dedupe against per-event Redis keys (overlapping windows).
      4. Persist NEW events to wallet_memory.attack_events.
      5. Push Redis attack_alert:{chain}:{token} for real-time consumers.
      6. Send Telegram scanner alert when confidence == HIGH.
      7. Mark attacker wallets as whale in dex_events.

    Args:
        chain: Chain name or "all" (default).
        hours_back: Scan window in hours (default 1).
        notify: Push Redis/Telegram/whale alerts (default True).
        force_fresh: Bypass caches.

    Returns:
        Summary dict with counts per stage.
    """
    summary: dict[str, Any] = {
        "check": "attack_detection",
        "result": "unavailable",
        "source": "own",
        "chains_scanned": [],
        "events": [],
        "events_new": 0,
        "inserted": 0,
        "alerts_pushed": 0,
        "telegram_sent": 0,
        "whale_marked": 0,
        "cached": False,
        "duration_ms": 0.0,
    }
    _start = time.monotonic()

    chains = [chain] if chain and chain != "all" else list(ALL_CHAINS)
    chains = [_normalize_chain(c) for c in chains]

    try:
        # 1. Redis window cache — skip re-scanning the same blocks
        if not force_fresh:
            cache_hit = await _check_window_cache(chains, hours_back)
            if cache_hit:
                summary["result"] = "scanned"
                summary["cached"] = True
                summary["duration_ms"] = round((time.monotonic() - _start) * 1000, 1)
                return summary

        all_events: list[dict[str, Any]] = []
        for chain_name in chains:
            try:
                sandwich = await detect_sandwiches(chain_name, hours_back, force_fresh)
                pump_dump = await detect_pump_dumps(chain_name, 24, force_fresh)
                flash_loan = await detect_flash_loans(chain_name, hours_back, force_fresh)
                all_events.extend(sandwich)
                all_events.extend(pump_dump)
                all_events.extend(flash_loan)
                summary["chains_scanned"].append(chain_name)
            except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
                logger.warning("attack detection failed for %s: %s", chain_name, exc)
                continue

        if not all_events:
            summary["result"] = "scanned"
            summary["events"] = []
            summary["duration_ms"] = round((time.monotonic() - _start) * 1000, 1)
            await _store_window_cache(chains, hours_back)
            return summary

        summary["events"] = all_events

        # 2-3. Dedupe against per-event Redis keys (overlapping scan windows)
        new_events = await _dedupe_new_events(all_events)
        summary["events_new"] = len(new_events)
        if not new_events:
            summary["result"] = "scanned"
            summary["duration_ms"] = round((time.monotonic() - _start) * 1000, 1)
            await _store_window_cache(chains, hours_back)
            return summary

        # 4. Persist to wallet_memory.attack_events
        summary["inserted"] = _insert_attack_events(new_events)

        # 5-7. Alert pipeline
        if notify:
            summary["alerts_pushed"] = await _push_redis_alerts(new_events)
            summary["telegram_sent"] = await _send_telegram_alerts(new_events)
            summary["whale_marked"] = await _mark_attacker_whale(new_events)

        await _store_window_cache(chains, hours_back)
        summary["result"] = "scanned"
    except (
        httpx.HTTPError,
        ConnectionError,
        OSError,
        TimeoutError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
        RedisError,
    ) as exc:
        logger.warning("run_attack_detection failed: %s", exc)
        summary["result"] = "error"
        summary["error"] = str(exc)[:200]

    summary["duration_ms"] = round((time.monotonic() - _start) * 1000, 1)
    logger.info("attack_detection_run", chains=summary["chains_scanned"], new=summary["events_new"])
    return summary


# ═══════════════════════════════════════════════════════════════════════════
# Persistence + alert wiring
# ═══════════════════════════════════════════════════════════════════════════


def _insert_attack_events(events: list[dict[str, Any]]) -> int:
    """Insert attack events into wallet_memory.attack_events. Returns rows inserted."""
    if not events:
        return 0
    client = _get_ch_client()
    if client is None:
        return 0
    if not _ensure_attack_table():
        return 0

    now_ts = datetime.now(UTC)
    rows = [
        (
            ev.get("attack_type", "unknown"),
            ev.get("confidence", "LOW"),
            ev.get("chain", ""),
            int(ev.get("block_number", 0) or 0),
            ev.get("tx_hashes", []) or [],
            ev.get("token_address", ""),
            ev.get("pair_address", ""),
            ev.get("value_usd"),
            now_ts,
        )
        for ev in events
    ]
    try:
        return cast(
            "int",
            client.execute(
                f"INSERT INTO {ATTACK_TABLE} ({', '.join(_ATTACK_INSERT_COLS)}) VALUES",
                rows,
                settings={"max_execution_time": MAX_EXECUTION_TIME},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.warning("attack_events insert failed: %s", exc)
        return 0


async def _push_redis_alerts(events: list[dict[str, Any]]) -> int:
    """Push attack_alert:{chain}:{token} keys for real-time consumers."""
    redis = await _get_redis()
    if redis is None:
        return 0
    pushed = 0
    try:
        for ev in events:
            key = f"attack_alert:{ev.get('chain', '')}:{ev.get('token_address', '')}"
            payload = {
                "attack_type": ev.get("attack_type"),
                "confidence": ev.get("confidence"),
                "chain": ev.get("chain"),
                "block_number": ev.get("block_number"),
                "tx_hashes": ev.get("tx_hashes", []),
                "token_address": ev.get("token_address"),
                "pair_address": ev.get("pair_address"),
                "value_usd": ev.get("value_usd"),
                "detected_at": datetime.now(UTC).isoformat(),
            }
            await redis.rpush(key, json.dumps(payload))
            await redis.expire(key, CACHE_TTL_SECONDS)
            await redis.ltrim(key, -50, -1)  # keep last 50 alerts per key
            pushed += 1
        return pushed
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.warning("attack redis push failed: %s", exc)
        return pushed


async def _send_telegram_alerts(events: list[dict[str, Any]]) -> int:
    """Send Telegram scanner alerts for HIGH-confidence attack events."""
    sent = 0
    for ev in events:
        if ev.get("confidence") != "HIGH":
            continue
        try:
            from app.domains.telegram.alert_hooks import send_scanner_alert

            attack_label = str(ev.get("attack_type", "attack")).upper()
            alert_name = "Attack Alert"
            threats = [f"{ev.get('attack_type')} attack detected", *ev.get("evidence", [])[:4]]
            await send_scanner_alert(
                token_address=ev.get("token_address", ""),
                chain=ev.get("chain", ""),
                risk_score=100.0,
                threats=threats,
                token_name=alert_name,
                token_symbol=attack_label,
            )
            sent += 1
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            logger.debug("attack telegram alert failed: %s", exc)
    return sent


async def _mark_attacker_whale(events: list[dict[str, Any]]) -> int:
    """Mark attacker wallets as whale in dex_events (ClickHouse ALTER UPDATE).

    Only sandwich events carry an attacker (front-run sender == back-run
    sender). Grouped per chain so the mutation touches the right rows.
    """
    attackers_by_chain: dict[str, set[str]] = {}
    for ev in events:
        attacker = str(ev.get("attacker", "") or "").lower()
        if ev.get("attack_type") == "sandwich" and attacker:
            attackers_by_chain.setdefault(str(ev.get("chain", "")), set()).add(attacker)
    if not attackers_by_chain:
        return 0
    client = _get_ch_client()
    if client is None:
        return 0

    def _query(chain_name: str, attackers: list[str]) -> int:
        try:
            return cast(
                "int",
                client.execute(
                    f"""
                ALTER TABLE {DEX_EVENTS_TABLE}
                UPDATE whale = 1
                WHERE chain = %(chain)s
                  AND sender IN %(attackers)s
                SETTINGS mutations_sync = 1
                """,
                    {"chain": chain_name, "attackers": attackers},
                    settings={"max_execution_time": MAX_EXECUTION_TIME},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.warning("whale marking failed for %s: %s", chain_name, exc)
            return 0

    marked = 0
    for chain_name, attackers in attackers_by_chain.items():
        try:
            marked += await run_sync(_query, chain_name, list(attackers))
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
            logger.warning("whale marking async failed for %s: %s", chain_name, exc)
    return marked


# ═══════════════════════════════════════════════════════════════════════════
# Caching (in-process + Redis window cache + per-event dedup)
# ═══════════════════════════════════════════════════════════════════════════


def _cache_key(parts: tuple[str, ...]) -> str:
    return ":".join(parts)


async def _check_window_cache(chains: list[str], hours_back: int) -> bool:
    """Return True when every chain's scan window is already cached in Redis."""
    redis = await _get_redis()
    if redis is None:
        return False
    try:
        for chain_name in chains:
            key = _cache_key(("attack_detect_window", chain_name, str(hours_back)))
            if not await redis.exists(key):
                return False
        return True
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("window cache check failed: %s", exc)
        return False


async def _store_window_cache(chains: list[str], hours_back: int) -> None:
    """Cache the scan window in Redis (TTL 15m) so we never re-scan the same blocks."""
    redis = await _get_redis()
    if redis is None:
        return
    try:
        payload = json.dumps({"hours_back": hours_back, "scanned_at": datetime.now(UTC).isoformat()})
        for chain_name in chains:
            key = _cache_key(("attack_detect_window", chain_name, str(hours_back)))
            await redis.set(key, payload, ex=CACHE_TTL_SECONDS)
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("window cache store failed: %s", exc)


async def _dedupe_new_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only events not seen in the last 15m (Redis SET NX per event)."""
    redis = await _get_redis()
    if redis is None:
        return events  # no Redis → allow (in-process cache still guards)

    new_events: list[dict[str, Any]] = []
    try:
        for ev in events:
            dedup_key = _cache_key(
                (
                    "attack_seen",
                    ev.get("chain", ""),
                    ev.get("attack_type", ""),
                    str(ev.get("block_number", 0)),
                    ev.get("pair_address", ""),
                )
            )
            created = await redis.set(dedup_key, "1", nx=True, ex=CACHE_TTL_SECONDS)
            if created:
                new_events.append(ev)
        return new_events
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("event dedup failed: %s", exc)
        return events


# ═══════════════════════════════════════════════════════════════════════════
# USD enrichment (best-effort, uses our own price oracle)
# ═══════════════════════════════════════════════════════════════════════════


async def _enrich_usd(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Estimate value_usd per event using the own-dex-data price oracle.

    Uses each event's raw token0 amount (``raw_amount``) x token TWAP price.
    Cached per token; failures degrade to None (never raises).
    """
    for ev in events:
        token = ev.get("token_address", "")
        raw_amount = _to_float(ev.get("raw_amount"))
        if not token or raw_amount <= 0:
            continue
        usd = await _estimate_usd(token, ev["chain"], raw_amount)
        if usd is not None:
            ev["value_usd"] = usd
    return events


async def _estimate_usd(token_address: str, chain: str, raw_amount: float) -> float | None:
    """Convert a raw token amount to USD using our own price oracle."""
    if not token_address or raw_amount <= 0:
        return None
    try:
        price_usd = _USD_PRICE_CACHE.get(token_address)
        if price_usd is None:
            price = await get_token_price_simple(token_address, chain, lookback_minutes=60)
            price_usd = float(price.get("price_usd") or 0)
            if price_usd <= 0:
                return None
            _USD_PRICE_CACHE[token_address] = price_usd
            if len(_USD_PRICE_CACHE) > _USD_PRICE_CACHE_MAX:
                _USD_PRICE_CACHE.pop(next(iter(_USD_PRICE_CACHE)), None)
        amt = _normalize_amount(raw_amount, token_address, chain)
        if amt <= 0:
            return None
        return round(amt * price_usd, 2)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("attack_usd_estimate_failed: %s", exc)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# Periodic job + CLI
# ═══════════════════════════════════════════════════════════════════════════


async def run_periodic_detection(hours_back: int = 1, notify: bool = True) -> dict[str, Any]:
    """Run attack detection across all chains — the 5-minute cron entry point."""
    return await run_attack_detection(chain="all", hours_back=hours_back, notify=notify)


def get_cache_stats() -> dict[str, Any]:
    """Return cache/connection stats for monitoring."""
    return _common_get_cache_stats(
        _RESULT_CACHE,
        _RESULT_CACHE_MAX,
        CACHE_TTL_SECONDS,
        extra={
            "usd_price_cache_size": len(_USD_PRICE_CACHE),
            "clickhouse_connected": _ch_client is not None,
            "redis_connected": _redis_client is not None,
            "max_execution_time": MAX_EXECUTION_TIME,
        },
    )


def clear_cache() -> None:
    """Clear in-process caches (for testing)."""
    _common_clear_cache(_RESULT_CACHE, _USD_PRICE_CACHE)


async def main() -> None:
    """CLI entry point — run periodic detection once and print a summary.

    Schedule via cron/systemd every 5 minutes:
        python -m app.domains.scanner.engines.attack_detector
    """
    import argparse

    parser = argparse.ArgumentParser(description="C6 Time-Based Attack Detection (ClickHouse-only)")
    parser.add_argument("--chain", default="all", help="Chain name or 'all'")
    parser.add_argument("--hours-back", type=int, default=1, help="Scan window in hours")
    parser.add_argument("--no-notify", action="store_true", help="Skip Redis/Telegram/whale wiring")
    parser.add_argument("--force-fresh", action="store_true", help="Bypass caches")
    args = parser.parse_args()

    result = await run_attack_detection(
        chain=args.chain,
        hours_back=args.hours_back,
        notify=not args.no_notify,
        force_fresh=args.force_fresh,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
