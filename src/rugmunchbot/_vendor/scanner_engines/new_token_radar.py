# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/new_token_radar.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
C3 — New Token Radar (v1) — New Token Discovery + Early Warning
===============================================================
Detects newly deployed tokens from our own ClickHouse ``deployer_events``
within the last N minutes, runs a fast risk scan on each, scores it
(CRITICAL / HIGH / WARNING / INFO), and pushes CRITICAL/HIGH alerts to
Telegram — before the crowd arrives.

Data sources (all LOCAL / free):
  - ``wallet_memory.deployer_events`` — new deployments (polled)
  - ``fingerprint_deployer`` (C7)     — deployer behavioral risk bucket
  - ``launchpad_resolver``            — factory → real creator resolution
  - honeypot / liquidity checks       — scanner modules + own DEX data
  - ``alert_hooks.send_scanner_alert`` — Telegram alert rail

Launchpad awareness:
  When ``deployer_events`` records a known launchpad factory (pump.fun,
  bags.fm, ...) as the deployer, the radar resolves the real creator EOA via
  ``launchpad_resolver`` before scoring. The result carries ``launchpad``
  (e.g. ``"pump.fun"``) and ``launchpad_deployer`` (the resolved creator).
  Launchpad tokens are not automatically high-risk — only a poor creator
  history (REPEAT_RUG / FARM / high risk) or honeypot escalates them.

NOTE — F0 dependency (per spec): F0 replaces the contract/factory-as-deployer
with the real EOA deployer and adds ``pair_address`` / ``token0`` / ``token1``
columns. Until F0 lands, the radar keys on ``token_address`` from
``deployer_events`` (the pair/token contract) and treats it as the new token.
Deployer-risk scoring is therefore approximate until F0 corrects the
deployer attribution — launchpad resolution closes part of that gap today.

Caching / dedup:
  - Result cache: Redis ``new_token_radar:{chain}`` (TTL 60s) — the periodic
    poller can read this instead of re-scanning.
  - Alert dedup: Redis ``new_token_radar:alerted:{chain}`` set (TTL 24h) so a
    CRITICAL/HIGH token is alerted once, not on every poll.
  - In-process mirror cache consistent with the other own-data engines.

Usage:
    from rugmunchbot._vendor.scanner_engines.new_token_radar import scan_new_tokens

    results = await scan_new_tokens("ethereum", lookback_minutes=5)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import httpx
from redis.exceptions import RedisError

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from app.domains.databus.providers.chains.lite_chain_indexer import (
    _get_ch_client,
    _normalize_chain,
)
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    redis_get,
    redis_set,
    run_sync,
    to_iso,
)

# Legacy module-local names kept so existing unit tests that patch
# ``new_token_radar._redis_get`` / ``new_token_radar._redis_set`` keep
# intercepting the shared implementation.
_redis_get = redis_get
_redis_set = redis_set

logger = get_logger(__name__)

# ── Cache / dedup ─────────────────────────────────────────────────────────
REDIS_CACHE_TTL_SECONDS = 60  # result cache for the poller
REDIS_ALERT_TTL_SECONDS = 86400  # alert dedup window (24h)
REDIS_PREFIX = "new_token_radar"
_RADAR_CACHE: dict[str, dict[str, Any]] = {}
_RADAR_CACHE_TTL = 60
_RADAR_CACHE_MAX = 100

# ── Limits / time-boxing ───────────────────────────────────────────────────
_QUERY_TIMEOUT_SECONDS = 10
_MAX_NEW_TOKENS = 30
_SCAN_PER_TOKEN_TIMEOUT = 12.0  # fast scan: honeypot + deployer + liquidity
_MIN_LIQUIDITY_WARNING_USD = 5_000.0  # below this → liquidity risk
_MIN_LIQUIDITY_HIGH_USD = 1_000.0


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def scan_new_tokens(
    chain: str = "ethereum",
    lookback_minutes: int = 5,
    notify: bool = True,
    force_fresh: bool = False,
) -> list[dict[str, Any]]:
    """Scan for tokens deployed in the last N minutes and score each one.

    Args:
        chain: Chain to poll (ethereum, bsc, base, ...).
        lookback_minutes: Window of recent deployments to consider.
        notify: Push CRITICAL/HIGH alerts to Telegram (default True).
        force_fresh: Bypass the result cache (re-scan).

    Returns:
        List of scored radar results (max ``_MAX_NEW_TOKENS``), each::

            {
                "token_address": "0x...",
                "chain": "ethereum",
                "deployer": "0x...",              # real creator when resolved
                "launchpad": "pump.fun" | None,   # launchpad flag
                "launchpad_deployer": "0x...",    # resolved real creator EOA
                "deploy_tx": "0x...",
                "deployed_at": "iso",
                "level": "CRITICAL" | "HIGH" | "WARNING" | "INFO",
                "score": 0-100,
                "signals": {deployer_risk, liquidity_usd, is_honeypot, launchpad, ...},
                "evidence": [...]
            }

        Returns an empty list (never raises) when ClickHouse is unreachable
        or no new deployments exist in the window.
    """
    chain_norm = _normalize_chain(chain)
    if not chain_norm:
        logger.debug("new_token_radar: unknown chain %s", chain)
        return []

    # ── Result cache for the poller ──
    cache_key = f"{REDIS_PREFIX}:{chain_norm}"
    if not force_fresh:
        cached = await _redis_get(cache_key)
        if cached is not None:
            return cached

    # ── In-process mirror ──
    if not force_fresh and cache_key in _RADAR_CACHE:
        entry = _RADAR_CACHE[cache_key]
        if time.time() - entry.get("_cached_at", 0) < _RADAR_CACHE_TTL:
            return entry.get("results", [])

    _start = time.monotonic()
    try:
        # ── 1. Poll deployer_events ──
        deployments = await run_sync(_query_new_deployments, chain_norm, lookback_minutes)
        if not deployments:
            results: list[dict[str, Any]] = []
            await _redis_set(cache_key, results, REDIS_CACHE_TTL_SECONDS)
            logger.info("new_token_radar_run", chain=chain_norm, new=0, ms=0.0)
            return results

        # Dedupe by token_address (keep earliest deployment)
        seen: dict[str, dict[str, Any]] = {}
        for dep in deployments:
            token = str(dep.get("token_address") or "").lower()
            if not token:
                continue
            if token not in seen or dep.get("block_timestamp", "") < seen[token].get("block_timestamp", ""):
                seen[token] = dep

        new_tokens = list(seen.values())[:_MAX_NEW_TOKENS]

        # ── 2. Fast scan each new token (bounded, parallel) ──
        tasks = [asyncio.create_task(_fast_scan_token(tok, chain_norm)) for tok in new_tokens]
        scanned = await asyncio.gather(*tasks, return_exceptions=True)

        results = []
        for _tok, outcome in zip(new_tokens, scanned, strict=False):
            if isinstance(outcome, BaseException) or outcome is None:
                continue
            results.append(outcome)

        # ── 3. Sort by risk (CRITICAL first, then score desc) ──
        level_order = {"CRITICAL": 0, "HIGH": 1, "WARNING": 2, "INFO": 3}
        results.sort(key=lambda r: (level_order.get(r.get("level", "INFO"), 9), -float(r.get("score", 0))))

        # ── 4. Alert CRITICAL/HIGH via Telegram (deduped) ──
        if notify:
            alerted = await _alert_high_risk(results, chain_norm)

        await _redis_set(cache_key, results, REDIS_CACHE_TTL_SECONDS)
        _RADAR_CACHE[cache_key] = {"results": results, "_cached_at": time.time()}
        if len(_RADAR_CACHE) > _RADAR_CACHE_MAX:
            _RADAR_CACHE.pop(next(iter(_RADAR_CACHE)), None)

        _elapsed_ms = (time.monotonic() - _start) * 1000
        logger.info(
            "new_token_radar_run",
            chain=chain_norm,
            new=len(results),
            alerts=len(alerted) if notify else 0,
            ms=round(_elapsed_ms, 1),
        )
        return results

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
        logger.debug("new_token_radar_failed: %s", exc)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# FAST SCAN + SCORING
# ═══════════════════════════════════════════════════════════════════════════


async def _fast_scan_token(deployment: dict[str, Any], chain: str) -> dict[str, Any]:
    """Run honeypot + deployer reputation + liquidity checks for one token.

    Time-boxed so a slow external check cannot block the poll loop.

    Launchpad awareness:
      When the recorded deployer is a known launchpad factory (pump.fun,
      bags.fm, ...), we resolve the real creator EOA first and score the
      fingerprint against the real deployer. Launchpad tokens are NOT
      automatically high-risk — most legitimate projects launch this way — but
      a real deployer with a poor history still escalates the risk level.
    """
    token_address = str(deployment.get("token_address") or "").lower()
    deployer = str(deployment.get("deployer") or "").lower()
    signals: dict[str, Any] = {}
    evidence: list[dict[str, Any]] = []

    try:
        # ── Launchpad / universal deployer resolution (before scoring) ──
        # If the deployer is a known launchpad factory, trace back to the real
        # creator EOA. For non-factory deployers, the UNIVERSAL resolver still
        # runs: it uses the indexer-resolved deployer_eoa or the creation tx
        # sender structurally (works for ANY platform/chain) and NEVER returns
        # None, so scans get the best available deployer.
        launchpad: str | None = None
        real_deployer: str | None = None
        try:
            from rugmunchbot._vendor.scanner_engines.launchpad_resolver import (
                lookup_launchpad,
                resolve_deployer_universal,
            )

            launchpad = lookup_launchpad(chain, deployer)
            resolved = await asyncio.wait_for(
                resolve_deployer_universal(
                    token_address,
                    chain,
                    deployer,
                    deploy_tx=deployment.get("tx_hash"),
                ),
                timeout=6.0,
            )
            improved = bool(
                resolved
                and resolved.get("real_deployer")
                and (
                    resolved.get("resolved")
                    or (
                        resolved.get("real_deployer")
                        and str(resolved["real_deployer"]).lower() != str(deployer).lower()
                    )
                )
            )
            # Only claim a resolved deployer when the resolver actually
            # improved on the recorded deployer. A passthrough (resolved=False,
            # real_deployer == the factory itself) means resolution FAILED —
            # reporting the factory as launchpad_deployer would be misleading.
            if resolved and resolved.get("real_deployer") and improved:
                real_deployer = str(resolved["real_deployer"])
                signals["launchpad_deployer"] = real_deployer
                signals["launchpad_confidence"] = resolved.get("confidence")
                signals["deployer_resolve_method"] = resolved.get("method")
                evidence.append(
                    {
                        "kind": "launchpad_resolution",
                        "launchpad": launchpad,
                        "factory": deployer,
                        "real_deployer": real_deployer,
                        "evidence_tx": resolved.get("evidence_tx"),
                        "confidence": resolved.get("confidence"),
                        "method": resolved.get("method"),
                    }
                )
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            RuntimeError,
            CHDriverError,
        ) as exc:
            logger.debug("radar_launchpad_resolve_failed %s: %s", token_address[:16], exc)

        if launchpad is not None:
            signals["launchpad"] = launchpad
            # Score against the REAL creator, not the factory.
            if real_deployer:
                deployer = real_deployer.lower() if chain != "solana" else real_deployer

        # ── Deployer reputation (C7 fingerprint — local, fast) ──
        try:
            from rugmunchbot._vendor.scanner_engines.deployer_fingerprint import fingerprint_deployer

            fp = await asyncio.wait_for(fingerprint_deployer(deployer, chain), timeout=6.0)
            if fp and fp.get("result") == "scanned":
                signals["deployer_bucket"] = fp.get("risk_bucket")
                signals["deployer_risk_score"] = float(fp.get("risk_score", 50) or 50)
                signals["deployer_deployments"] = int(fp.get("total_deployments", 0) or 0)
                evidence.append(
                    {
                        "kind": "deployer_fingerprint",
                        "deployer": deployer,
                        "risk_bucket": fp.get("risk_bucket"),
                        "risk_score": fp.get("risk_score"),
                    }
                )
            else:
                signals["deployer_bucket"] = "UNKNOWN"
                signals["deployer_risk_score"] = 50.0
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
            logger.debug("radar_fingerprint_failed %s: %s", token_address[:16], exc)
            signals["deployer_bucket"] = "UNKNOWN"
            signals["deployer_risk_score"] = 50.0

        # ── Honeypot check (scanner module — own data first) ──
        try:
            from app.domains.scanner.modules.security import _check_honeypot_is

            hp = await asyncio.wait_for(_check_honeypot_is(token_address, chain), timeout=8.0)
            if hp and hp.get("result") == "scanned":
                signals["is_honeypot"] = bool(hp.get("is_honeypot", False))
                signals["honeypot_risk_score"] = float(hp.get("risk_score", 0) or 0)
                signals["buy_tax_pct"] = hp.get("buy_tax_pct")
                signals["sell_tax_pct"] = hp.get("sell_tax_pct")
                evidence.append(
                    {
                        "kind": "honeypot",
                        "is_honeypot": hp.get("is_honeypot"),
                        "risk_score": hp.get("risk_score"),
                        "findings": (hp.get("findings") or [])[:3],
                    }
                )
            else:
                signals["is_honeypot"] = False
                signals["honeypot_risk_score"] = 0.0
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            logger.debug("radar_honeypot_failed %s: %s", token_address[:16], exc)
            signals["is_honeypot"] = False
            signals["honeypot_risk_score"] = 0.0

        # ── Liquidity check (own DEX data first, DexScreener fallback) ──
        signals["liquidity_usd"] = await _check_liquidity(token_address, chain)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
        logger.debug("radar_fast_scan_failed %s: %s", token_address[:16], exc)

    level, score = _score_new_token(signals)
    evidence.append(
        {
            "kind": "deployment",
            "deploy_tx": deployment.get("tx_hash"),
            "deployed_at": deployment.get("block_timestamp"),
            "token_address": token_address,
        }
    )

    return {
        "token_address": token_address,
        "chain": chain,
        "deployer": deployer or None,
        "launchpad": signals.get("launchpad"),
        "launchpad_deployer": signals.get("launchpad_deployer"),
        "deploy_tx": deployment.get("tx_hash"),
        "deployed_at": deployment.get("block_timestamp"),
        "level": level,
        "score": score,
        "signals": signals,
        "evidence": evidence[:6],
    }


async def _check_liquidity(token_address: str, chain: str) -> float:
    """Return liquidity USD for a token — single source: unified data sources.

    Routes through ``unified._data_sources.get_token_liquidity`` (canonical
    ``own_dex_data`` → ClickHouse dex_events, DexScreener fallback) instead of
    crafting its own DexScreener call. The shared getter returns the same
    DexScreener-compatible payload ``modules._core._fetch_dexscreener`` used
    to return, so extraction is unchanged.
    """
    try:
        from rugmunchbot._vendor.unified_scanner._data_sources import get_token_liquidity

        ds = await asyncio.wait_for(get_token_liquidity(token_address, chain), timeout=6.0)
        if ds and ds.get("pairs"):
            return float(ds["pairs"][0].get("liquidity", {}).get("usd", 0) or 0)
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
        logger.debug("radar_liquidity_failed %s: %s", token_address[:16], exc)
    return 0.0


async def _lookup_token_symbol(token_address: str, chain: str) -> str | None:
    """Best-effort token symbol lookup from the token_meta_ref table.

    evm_transfers no longer carries token_symbol (enrichment moved to
    wallet_memory.token_meta_ref — one row per token), so we read the
    symbol from the reference table (zero external cost).
    Returns None when unavailable.
    """
    if not token_address or not chain:
        return None
    rows = await run_sync(_query_token_symbol, token_address, chain)
    if rows and rows[0] and rows[0][0]:
        symbol = str(rows[0][0]).strip()
        return symbol or None
    return None


def _score_new_token(signals: dict[str, Any]) -> tuple[str, int]:
    """Score a new token and assign CRITICAL / HIGH / WARNING / INFO.

    Heuristics (v1 — tuned on historical correlation per spec):
      - Honeypot flag → CRITICAL (100)
      - Deployer bucket REPEAT_RUG / FARM OR deployer risk >= 80 → CRITICAL
      - Deployer risk >= 60 OR liquidity < $1K → HIGH
      - Deployer NEW/UNKNOWN OR liquidity < $5K → WARNING
      - Otherwise INFO

    Launchpad adjustment (pump.fun / bags.fm / ...):
      Launchpad tokens are NOT automatically high-risk — most legitimate
      projects launch this way. The factory itself is never scored; the radar
      resolves the real creator before this point. A launchpad token whose
      creator resolved to a clean profile is downgraded one notch (INFO instead
      of WARNING), while a creator with a poor history (REPEAT_RUG / FARM /
      high risk) is still flagged CRITICAL/HIGH. When the creator could not be
      resolved, we stay neutral (WARNING) rather than escalating.
    """
    is_honeypot = bool(signals.get("is_honeypot", False))
    deployer_risk = float(signals.get("deployer_risk_score", 50.0) or 50.0)
    bucket = str(signals.get("deployer_bucket", "UNKNOWN"))
    liquidity = float(signals.get("liquidity_usd", 0.0) or 0.0)
    launchpad = signals.get("launchpad")

    if is_honeypot:
        return "CRITICAL", 100
    if bucket in ("REPEAT_RUG", "FARM") or deployer_risk >= 80:
        return "CRITICAL", min(95, int(deployer_risk))
    if deployer_risk >= 60 or liquidity < _MIN_LIQUIDITY_HIGH_USD:
        return "HIGH", max(60, min(89, int(deployer_risk)))
    if bucket in ("NEW", "UNKNOWN") or liquidity < _MIN_LIQUIDITY_WARNING_USD:
        # Launchpad + fresh/clean creator (resolved) → INFO, not WARNING:
        # launchpad launches are the normal path for legitimate projects.
        if launchpad and signals.get("launchpad_deployer") and bucket != "UNKNOWN":
            return "INFO", max(20, min(39, int(deployer_risk) // 2))
        return "WARNING", max(40, min(70, int(deployer_risk)))
    return "INFO", min(39, int(deployer_risk) // 2)


# ═══════════════════════════════════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════════════════════════════════


async def _alert_high_risk(results: list[dict[str, Any]], chain: str) -> list[str]:
    """Push CRITICAL/HIGH tokens to Telegram once (dedup via Redis set)."""
    high_risk = [r for r in results if r.get("level") in ("CRITICAL", "HIGH")]
    if not high_risk:
        return []

    alerted: list[str] = []
    for r in high_risk:
        token = r.get("token_address", "")
        if not token:
            continue
        if await _was_alerted(chain, token):
            continue

        try:
            from app.domains.telegram.alert_hooks import send_scanner_alert

            signals = r.get("signals", {})
            threats = []
            if signals.get("is_honeypot"):
                threats.append("honeypot")
            bucket = signals.get("deployer_bucket")
            if bucket in ("REPEAT_RUG", "FARM"):
                threats.append(f"high_risk_deployer:{bucket}")
            if float(signals.get("liquidity_usd", 0) or 0) < _MIN_LIQUIDITY_WARNING_USD:
                threats.append("low_liquidity")

            symbol = str(r.get("token_symbol") or "") or await _lookup_token_symbol(token, chain) or f"{token[:8]}..."
            await send_scanner_alert(
                token_address=token,
                chain=chain,
                risk_score=float(r.get("score", 60) or 60),
                threats=threats or ["new_token_radar"],
                token_name=f"New {r.get('level', 'HIGH').title()} Token",
                token_symbol=symbol,
            )
            alerted.append(token)
            await _mark_alerted(chain, token)
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            logger.debug("radar_alert_failed %s: %s", token[:16], exc)

    return alerted


async def _was_alerted(chain: str, token: str) -> bool:
    """Return True when this token was already alerted (Redis set)."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is None:
            return False
        key = f"{REDIS_PREFIX}:alerted:{chain}:{token}"
        return bool(await r.exists(key))
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("radar_alert_dedup_check_failed: %s", exc)
        return False


async def _mark_alerted(chain: str, token: str) -> None:
    """Mark a token as alerted for the dedup window."""
    try:
        from rugmunchbot._vendor.redis_client import get_redis_async

        r = get_redis_async()
        if r is None:
            return
        key = f"{REDIS_PREFIX}:alerted:{chain}:{token}"
        await r.setex(key, REDIS_ALERT_TTL_SECONDS, "1")
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("radar_alert_dedup_set_failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


def _query_new_deployments(chain: str, lookback_minutes: int) -> list[dict[str, Any]]:
    """Return deployer_events rows deployed in the last N minutes."""
    client = _get_ch_client()
    if client is None:
        return []

    params: dict[str, Any] = {
        "chain": chain,
        "minutes": max(1, int(lookback_minutes)),
        "limit": _MAX_NEW_TOKENS * 2,
    }
    try:
        rows = client.execute(
            """
            SELECT deployer, token_address, chain, block_number,
                   block_timestamp, tx_hash, type, factory
            FROM wallet_memory.deployer_events
            WHERE chain = %(chain)s
              AND block_timestamp >= now() - INTERVAL %(minutes)s MINUTE
            ORDER BY block_timestamp DESC
            LIMIT %(limit)s
            """,
            params,
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("radar_deployer_query_failed: %s", exc)
        return []

    deployments = []
    for row in rows:
        deployments.append(
            {
                "deployer": row[0],
                "token_address": row[1],
                "chain": row[2],
                "block_number": row[3],
                "block_timestamp": to_iso(row[4]),
                "tx_hash": row[5],
                "type": row[6],
                "factory": row[7],
            }
        )
    return deployments


def _query_token_symbol(token_address: str, chain: str) -> list[tuple[Any, ...]]:
    """Return the most recent non-empty symbol from the token_meta_ref table.

    evm_transfers stores empty token_symbol on every row (enrichment lives
    in wallet_memory.token_meta_ref), so the reference table is the only
    source of truth for symbols.
    """
    client = _get_ch_client()
    if client is None:
        return []
    try:
        return cast(
            "list[tuple[Any, ...]]",
            client.execute(
                """
                SELECT symbol
                FROM wallet_memory.token_meta_ref
                WHERE chain = %(chain)s
                  AND token_address = %(token)s
                  AND symbol != ''
                ORDER BY last_updated DESC
                LIMIT 1
                """,
                {"token": token_address, "chain": chain},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            ),
        )
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("radar_token_symbol_query_failed %s: %s", token_address[:16], exc)
        return []


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _RADAR_CACHE,
        _RADAR_CACHE_MAX,
        _RADAR_CACHE_TTL,
        extra={
            "redis_cache_ttl_seconds": REDIS_CACHE_TTL_SECONDS,
            "alert_dedup_ttl_seconds": REDIS_ALERT_TTL_SECONDS,
            "clickhouse_connected": _get_ch_client() is not None,
        },
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_RADAR_CACHE)
