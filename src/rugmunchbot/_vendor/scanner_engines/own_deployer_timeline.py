# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/own_deployer_timeline.py (frozen copy; do not edit).
# Re-vendor: bash scripts/sync-from-live.sh
# ---
"""
Own Deployer Timeline — Prior-Token History for the Deep Scan
==============================================================
Competitor feature for the deep scan: surfaces the deployer's prior-token
history as a compact timeline line:

    👨‍💻 Deployer: 3 prior tokens · 2 rugged · funded from CEX · age 14d

Assembled from our own ClickHouse data:

  - prior tokens deployed        (deployer_events)
  - prior rug / risky count      (dex_events survival proxy — matured
                                  tokens that did NOT survive 7 days)
  - funded from                  (first funder + wallet_labels CEX lookup)
  - deployer age                 (first deployer event -> now)
  - deployer balance             (native net balance from evm_transfers /
                                  solana_spl_balance_changes)

Reuses the deployer-fingerprint query layer (``_deployer_fingerprint_queries``)
for deployment stats, per-token DEX survival, and the first-funder lookup, so
the numbers match the fingerprint engine exactly.

Fail-open by design: ``"unknown"`` / ``"—"`` for any component that is
genuinely unavailable. Never fabricates history.

Usage:
    from rugmunchbot._vendor.scanner_engines.own_deployer_timeline import get_own_deployer_timeline

    result = await get_own_deployer_timeline("0x...", "ethereum")
    # result = {
    #     "check": "deployer_timeline",
    #     "result": "scanned",
    #     "deployer_timeline_line": "👨‍💻 Deployer: 3 prior tokens · 2 rugged · funded from CEX · age 14d",
    #     "prior_tokens": 3,
    #     "rugged_count": 2,
    #     "funded_from": "CEX",
    #     "deployer_age_days": 14,
    #     "deployer_balance": "0.0",
    # }

Data Sources:
  Primary: wallet_memory.deployer_events (deployment stats + per-token history)
  Primary: wallet_memory.dex_events (DEX survival -> rugged count proxy)
  Primary: wallet_memory.evm_transfers / solana_spl_balance_changes (balance)
  Primary: wallet_memory.wallet_labels (funder CEX classification)
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_helpers import _classify_cex
from rugmunchbot._vendor.scanner_engines._deployer_fingerprint_queries import (
    _NATIVE_TOKEN_MARKERS,
    _query_deployer_stats,
    _query_funder,
    _query_funder_label,
    _query_per_token_deployments,
    _query_token_dex_stats,
)

logger = get_logger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────
_DEPLOYER_TIMELINE_CACHE: dict[str, dict[str, Any]] = {}
_DEPLOYER_TIMELINE_CACHE_TTL = 600  # 10 minutes — history changes slowly
_DEPLOYER_TIMELINE_CACHE_MAX = 200

#: A prior token counts as "rugged" when it matured (>= 7d old) but had zero
#: DEX survival past 7 days. Mirrors the fingerprint engine's survived_7d.
_MATURITY_DAYS = 7


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to the format used in ClickHouse tables."""
    return (chain or "").lower().strip()


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_deployer_timeline(
    deployer_address: str,
    chain: str = "",
    force_fresh: bool = False,
) -> dict[str, Any]:
    """Build the deployer prior-token timeline line.

    Args:
        deployer_address: Deployer wallet address (lowercased internally).
        chain: Optional chain filter (empty = all chains).
        force_fresh: Bypass the in-process cache.

    Returns:
        Dict with ``deployer_timeline_line`` plus the underlying metrics.
        Fail-open: ``"unknown"`` / ``"—"`` for genuinely unavailable data.
    """
    deployer_address = (deployer_address or "").lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not deployer_address:
        return _timeline_result(deployer_address, chain_norm, line="\U0001f468\u200d\U0001f4bb Deployer: \u2014")

    cache_key = f"{deployer_address}:{chain_norm or 'any'}"
    if not force_fresh and cache_key in _DEPLOYER_TIMELINE_CACHE:
        cached = _DEPLOYER_TIMELINE_CACHE[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _DEPLOYER_TIMELINE_CACHE_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict[str, Any] = {
        "check": "deployer_timeline",
        "result": "unavailable",
        "source": "own",
        "deployer": deployer_address,
        "chain": chain_norm or "any",
        "prior_tokens": "unknown",
        "rugged_count": "unknown",
        "funded_from": "unknown",
        "deployer_age_days": None,
        "deployer_balance": "unknown",
        "deployer_timeline_line": "\u2014",
        "confidence": 0.0,
    }

    try:
        stats = await run_sync(_query_deployer_stats, deployer_address, chain_norm)
        per_token = await run_sync(_query_per_token_deployments, deployer_address, chain_norm)
        dex_stats = await run_sync(_query_token_dex_stats, per_token, chain_norm)
        funder, funder_labels = await _funder_info(deployer_address, chain_norm)
        balance = await run_sync(_query_deployer_balance, deployer_address, chain_norm)
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("deployer_timeline_query_failed: %s", exc)
        result["deployer_timeline_line"] = "\U0001f468\u200d\U0001f4bb Deployer: unknown"
        return result

    # ── Prior tokens + rugged count ──
    prior_tokens: Any = "unknown"
    rugged_count: Any = "unknown"
    if stats and int(stats.get("total_deployments", 0) or 0) > 0:
        prior_tokens = int(stats.get("total_deployments", 0) or 0)

        now_naive = datetime.now(UTC).replace(tzinfo=None)
        rugged = 0
        judged = 0
        for tok in per_token or []:
            deployed_ts = tok.get("deployed_ts")
            if deployed_ts is None:
                continue
            try:
                deployed_naive = deployed_ts.replace(tzinfo=None) if deployed_ts.tzinfo else deployed_ts
            except (TypeError, ValueError):
                continue
            if deployed_naive + timedelta(days=_MATURITY_DAYS) > now_naive:
                continue  # too young to judge
            judged += 1
            last_swap = (dex_stats.get(tok["token_address"].lower()) or {}).get("last_swap_ts")
            survived = bool(last_swap and _is_after(last_swap, deployed_naive + timedelta(days=_MATURITY_DAYS)))
            if not survived:
                rugged += 1
        if judged > 0:
            rugged_count = rugged
        elif prior_tokens > 0:
            # No matured tokens yet — report the count we can see honestly.
            rugged_count = 0

    # ── Funded from (CEX / fresh / other) ──
    funded_from: Any = "unknown"
    if funder:
        is_cex, _cex_label = _classify_cex(funder_labels)
        if is_cex:
            funded_from = "CEX"
        elif funder.get("is_native"):
            funded_from = "wallet"
        else:
            funded_from = "wallet"

    # ── Deployer age ──
    age_days: Any = None
    if stats and stats.get("first_ts"):
        try:
            first_ts = stats["first_ts"]
            first_naive = first_ts.replace(tzinfo=None) if first_ts.tzinfo else first_ts
            age_days = max(int((datetime.now(UTC).replace(tzinfo=None) - first_naive).total_seconds() // 86400), 0)
        except (TypeError, ValueError):
            age_days = None

    # ── Balance ──
    deployer_balance: Any = "unknown"
    if balance is not None:
        deployer_balance = f"{balance:.4g}"

    result.update(
        {
            "result": "scanned",
            "prior_tokens": prior_tokens,
            "rugged_count": rugged_count,
            "funded_from": funded_from,
            "deployer_age_days": age_days,
            "deployer_balance": deployer_balance,
            "deployer_timeline_line": _build_timeline_line(
                prior_tokens, rugged_count, funded_from, age_days, deployer_balance
            ),
            "confidence": 0.7 if prior_tokens != "unknown" else 0.3,
        }
    )

    _cache_timeline(cache_key, result)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_deployer_timeline_latency",
        ms=round(_elapsed_ms, 1),
        deployer=deployer_address[:10],
        prior=result["prior_tokens"],
        rugged=result["rugged_count"],
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _funder_info(deployer_address: str, chain_norm: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """First funder of the deployer + its wallet_labels rows (CEX lookup)."""
    try:
        funder = await run_sync(_query_funder, deployer_address, chain_norm)
        labels: list[dict[str, Any]] = []
        if funder and funder.get("from_address"):
            labels = await run_sync(_query_funder_label, funder["from_address"])
        return funder, labels
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        RuntimeError,
        ValueError,
        KeyError,
        TypeError,
        CHDriverError,
    ) as exc:
        logger.debug("deployer_timeline_funder_failed: %s", exc)
        return None, []


def _is_after(value: Any, boundary: datetime) -> bool:
    """True when ``value`` (datetime or iso string) is after ``boundary``."""
    if isinstance(value, datetime):
        naive = value.replace(tzinfo=None) if value.tzinfo else value
        return naive > boundary
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        naive = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        return naive > boundary
    except (TypeError, ValueError):
        return False


def _query_deployer_balance(deployer_address: str, chain_norm: str) -> float | None:
    """Net native balance of the deployer (EVM) or SPL net (Solana).

    Returns ``None`` when the chain has no native-balance data in our
    ClickHouse (callers render "unknown" / "—").
    """
    if not chain_norm:
        return None
    try:
        from rugmunchbot._vendor.lite_chain_indexer import _get_ch_client

        client = _get_ch_client()
        if client is None:
            return None
        if chain_norm == "solana":
            rows = client.execute(
                """
                SELECT sum(toFloat64OrZero(delta))
                FROM wallet_memory.solana_spl_balance_changes
                WHERE owner = %(addr)s
                """,
                {"addr": deployer_address},
                settings={"max_execution_time": 10},
            )
            return float(rows[0][0] or 0) if rows and rows[0] else 0.0
        rows = client.execute(
            """
            SELECT sum(net) FROM (
                SELECT to_address AS addr, toFloat64OrZero(value) AS net
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND to_address = %(addr)s
                  AND lower(token_address) IN %(native)s
                UNION ALL
                SELECT from_address AS addr, -toFloat64OrZero(value) AS net
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s AND from_address = %(addr)s
                  AND lower(token_address) IN %(native)s
            )
            """,
            {"addr": deployer_address, "chain": chain_norm, "native": tuple(_NATIVE_TOKEN_MARKERS)},
            settings={"max_execution_time": 10},
        )
        return float(rows[0][0] or 0) if rows and rows[0] else 0.0
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("deployer_timeline_balance_failed: %s", exc)
        return None


def _build_timeline_line(
    prior_tokens: Any,
    rugged_count: Any,
    funded_from: Any,
    age_days: Any,
    deployer_balance: Any,
) -> str:
    """Assemble the compact timeline line with honest "unknown" / "—" values."""
    prior_s = str(prior_tokens) if prior_tokens != "unknown" else "\u2014"
    rugged_s = str(rugged_count) if rugged_count != "unknown" else "\u2014"
    funded_s = str(funded_from) if funded_from not in ("unknown", None) else "\u2014"
    age_s = f"{int(age_days)}d" if isinstance(age_days, int) else "\u2014"
    bal_s = f"bal {deployer_balance}" if deployer_balance not in ("unknown", None) else "bal \u2014"
    return (
        f"\U0001f468\u200d\U0001f4bb Deployer: {prior_s} prior tokens \u00b7 "
        f"{rugged_s} rugged \u00b7 funded from {funded_s} \u00b7 age {age_s} \u00b7 {bal_s}"
    )


def _timeline_result(deployer_address: str, chain_norm: str, line: str) -> dict[str, Any]:
    """Fail-open result shape (no deployer / unreachable)."""
    return {
        "check": "deployer_timeline",
        "result": "unavailable",
        "source": "own",
        "deployer": deployer_address,
        "chain": chain_norm or "any",
        "prior_tokens": "unknown",
        "rugged_count": "unknown",
        "funded_from": "unknown",
        "deployer_age_days": None,
        "deployer_balance": "unknown",
        "deployer_timeline_line": line,
        "confidence": 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CACHING
# ═══════════════════════════════════════════════════════════════════════════


def _cache_timeline(key: str, data: dict[str, Any]) -> None:
    """Cache deployer timeline data with timestamp."""
    data["_cached_at"] = time.time()
    _DEPLOYER_TIMELINE_CACHE[key] = data
    if len(_DEPLOYER_TIMELINE_CACHE) > _DEPLOYER_TIMELINE_CACHE_MAX:
        _DEPLOYER_TIMELINE_CACHE.pop(next(iter(_DEPLOYER_TIMELINE_CACHE)), None)


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        _DEPLOYER_TIMELINE_CACHE,
        _DEPLOYER_TIMELINE_CACHE_MAX,
        _DEPLOYER_TIMELINE_CACHE_TTL,
        extra={"maturity_days": _MATURITY_DAYS},
    )


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _common_clear_cache(_DEPLOYER_TIMELINE_CACHE)
