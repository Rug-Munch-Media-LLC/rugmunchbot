# VENDORED 2026-08-17 from rmi-backend/app/domains/scanner/engines/_attack_helpers.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Attack detection helpers — sandwich / pump & dump / flash loan detectors.

Extracted from ``engines/attack_detector.py`` (degodding wave 5, 2026-08-08)
so the three time-based detectors and their shared window-query scaffolding
over ``wallet_memory.dex_events`` live beside the other pure engine helpers
instead of bloating the orchestrator (persistence / alert wiring / caching).

This module is the ``SANDWICH DETECTOR`` + ``PUMP & DUMP DETECTOR`` +
``FLASH LOAN DETECTOR`` + ``USD enrichment`` sections of the detector.
Imports are intentionally one-directional: ``attack_detector`` imports these
helpers; the helpers never import ``attack_detector`` at module load.

Two call-time delegates are used so tests that patch
``attack_detector._get_ch_client`` / ``attack_detector._estimate_usd`` keep
working: the shared ClickHouse client and the USD enrichment live in the
orchestrator module (they also serve the persistence / alert layer there),
and are resolved at call time through the module attribute.
"""

from __future__ import annotations

import time
from typing import Any, cast

from rugmunchbot._vendor.logging_shim import get_logger
from rugmunchbot._vendor.scanner_engines._common import run_sync

logger = get_logger("attack_detector")

# ── Constants ────────────────────────────────────────────────────────────────
_CH_DB = "wallet_memory"
DEX_EVENTS_TABLE = f"{_CH_DB}.dex_events"
MAX_EXECUTION_TIME = 30  # seconds — applied to every ClickHouse query
CACHE_TTL_SECONDS = 15 * 60  # 15 minutes — avoid re-scanning the same blocks
MAX_EVENTS_PER_SCAN = 100  # cap on events per detector per scan

# Detection thresholds
SANDWICH_BALANCED_RATIO = 0.30  # |buy-sell|/max < 0.30 → balanced sandwich
PND_VOLUME_SPIKE_MULT = 10.0  # 4h volume > 10x trailing-24h baseline
PND_PRICE_SPIKE_PCT = 100.0  # price increase > 100% from baseline
PND_DRAWDOWN_PCT = 50.0  # drawdown > 50% from peak within 24h
PND_MIN_PAIR_SWAPS = 10  # ignore dust pairs
FLASH_REPAY_TOLERANCE = 0.10  # |in - out|/max(in,out) <= 10% → repay

# Supported chains (matches own_dex_data chain map + solana)
_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "solana": "solana",
}

# In-process per-detector result cache
_RESULT_CACHE: dict[str, dict[str, Any]] = {}
_RESULT_CACHE_MAX = 100


def _normalize_chain(chain: str) -> str:
    """Normalize a chain name to the format used in ClickHouse tables."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _get_ch_client() -> Any | None:
    """Call-time delegate to the shared ClickHouse client.

    The client lives in ``attack_detector`` (it also serves the persistence
    and whale-marking layer there). Resolved at call time so tests patching
    ``attack_detector._get_ch_client`` keep working.
    """
    from rugmunchbot._vendor.scanner_engines.attack_detector import _get_ch_client as _real

    return _real()


# ── Per-detector result cache (in-process) ─────────────────────────────────
def _get_cached(key: str) -> list[dict[str, Any]] | None:
    """Return cached detector results if fresh (< 15m)."""
    entry = _RESULT_CACHE.get(key)
    if entry is None:
        return None
    if time.time() - entry.get("_ts", 0) > CACHE_TTL_SECONDS:
        _RESULT_CACHE.pop(key, None)
        return None
    return entry.get("events")


def _set_cached(key: str, events: list[dict[str, Any]]) -> None:
    """Store detector results with a timestamp."""
    _RESULT_CACHE[key] = {"_ts": time.time(), "events": events}
    if len(_RESULT_CACHE) > _RESULT_CACHE_MAX:
        _RESULT_CACHE.pop(next(iter(_RESULT_CACHE)), None)


# ═══════════════════════════════════════════════════════════════════════════
# 1. SANDWICH DETECTOR
# ═══════════════════════════════════════════════════════════════════════════


async def detect_sandwiches(chain: str, hours_back: int = 1, force_fresh: bool = False) -> list[dict[str, Any]]:
    """Detect sandwich attacks from dex_events (same block, same pair).

    Pattern: attacker buy (amount0_in > 0) → victim swap → attacker sell
    (amount0_out > 0), all in the same block, same contract_address, with
    sender(A) == sender(C).

    Args:
        chain: Chain name (ethereum, bsc, ...).
        hours_back: Lookback window in hours (default 1 — periodic job window).
        force_fresh: Bypass the in-process cache.

    Returns:
        List of sandwich event dicts (max 100).
    """
    chain = _normalize_chain(chain)
    cache_key = f"sandwich:{chain}:{hours_back}"
    cached = _get_cached(cache_key)
    if not force_fresh and cached is not None:
        return cached

    events: list[dict[str, Any]] = []
    client = _get_ch_client()
    if client is None:
        return events

    def _query() -> list[tuple[Any, ...]]:
        params: dict[str, Any] = {
            "chain": chain,
            "hours": max(hours_back, 1),
            "limit": MAX_EVENTS_PER_SCAN,
        }
        try:
            return cast(
                "list[tuple[Any, ...]]",
                client.execute(
                    f"""
                SELECT
                    a.sender AS attacker, a.block_number, a.tx_hash AS buy_tx,
                    b.tx_hash AS victim_tx, c.tx_hash AS sell_tx,
                    a.amount0_in AS buy_amount, c.amount0_out AS sell_amount,
                    a.contract_address AS pair_address,
                    a.token0 AS token0, a.token1 AS token1,
                    a.block_timestamp AS block_timestamp,
                    c.log_index AS sell_log_index
                FROM {DEX_EVENTS_TABLE} a
                JOIN {DEX_EVENTS_TABLE} b
                    ON a.block_number = b.block_number
                   AND a.contract_address = b.contract_address
                   AND a.chain = b.chain
                JOIN {DEX_EVENTS_TABLE} c
                    ON a.block_number = c.block_number
                   AND a.contract_address = c.contract_address
                   AND a.chain = c.chain
                   AND a.sender = c.sender
                WHERE a.chain = %(chain)s
                  AND a.type = 'swap' AND b.type = 'swap' AND c.type = 'swap'
                  AND a.block_timestamp >= now() - INTERVAL %(hours)s HOUR
                  AND b.block_timestamp >= now() - INTERVAL %(hours)s HOUR
                  AND c.block_timestamp >= now() - INTERVAL %(hours)s HOUR
                  AND a.amount0_in > 0
                  AND c.amount0_out > 0
                  AND a.tx_hash < b.tx_hash
                  AND b.tx_hash < c.tx_hash
                  AND a.tx_hash != c.tx_hash
                ORDER BY a.block_number DESC
                LIMIT %(limit)s
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                    params,
                    settings={"max_execution_time": MAX_EXECUTION_TIME},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("sandwich query failed for %s: %s", chain, exc)
            return []

    rows = await run_sync(_query)
    for row in rows:
        try:
            attacker, block_number, buy_tx, victim_tx, sell_tx = row[0], row[1], row[2], row[3], row[4]
            buy_amount = _to_float(row[5])
            sell_amount = _to_float(row[6])
            pair_address = row[7]
            token0 = row[8]
            _token1 = row[9]
            block_ts = row[10]

            if buy_amount <= 0 or sell_amount <= 0:
                continue

            token_address = token0 or pair_address
            balance_ratio = abs(buy_amount - sell_amount) / max(buy_amount, sell_amount)
            if balance_ratio <= SANDWICH_BALANCED_RATIO and sell_amount >= buy_amount:
                confidence = "HIGH"
            elif balance_ratio <= SANDWICH_BALANCED_RATIO:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            events.append(
                {
                    "attack_type": "sandwich",
                    "confidence": confidence,
                    "chain": chain,
                    "block_number": int(block_number),
                    "tx_hashes": [str(buy_tx), str(victim_tx), str(sell_tx)],
                    "token_address": str(token_address).lower(),
                    "pair_address": str(pair_address).lower(),
                    "value_usd": None,  # enriched below
                    "raw_amount": buy_amount,  # token0 units of the attack leg
                    "attacker": str(attacker).lower(),
                    "evidence": [
                        f"same block {block_number}",
                        f"buy {buy_amount:.6g} / sell {sell_amount:.6g} token0",
                        f"victim tx {victim_tx}",
                    ],
                    "block_timestamp": str(block_ts),
                }
            )
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("sandwich row parse failed: %s", exc)
            continue

    # Enrich with USD estimates (capped — price oracle per token)
    events = await _enrich_usd(events[:MAX_EVENTS_PER_SCAN])

    _set_cached(cache_key, events)
    logger.info("attack_detector_sandwich", chain=chain, count=len(events), hours=hours_back)
    return events


# ═══════════════════════════════════════════════════════════════════════════
# 2. PUMP & DUMP DETECTOR
# ═══════════════════════════════════════════════════════════════════════════


async def detect_pump_dumps(chain: str, lookback_hours: int = 24, force_fresh: bool = False) -> list[dict[str, Any]]:
    """Detect pump-and-dump campaigns from dex_events per pair.

    Pattern over the lookback window (24h):
      - Volume spike:  4h volume > 10x trailing-24h baseline
      - Price spike:   price increase > 100% from baseline
      - Drawdown:      price drops > 50% from peak within 24h

    Args:
        chain: Chain name.
        lookback_hours: Analysis window (default 24).
        force_fresh: Bypass the in-process cache.

    Returns:
        List of pump_dump event dicts.
    """
    chain = _normalize_chain(chain)
    cache_key = f"pump_dump:{chain}:{lookback_hours}"
    cached = _get_cached(cache_key)
    if not force_fresh and cached is not None:
        return cached

    events: list[dict[str, Any]] = []
    client = _get_ch_client()
    if client is None:
        return events

    # ── Step 1: candidate pairs with meaningful swap activity ──
    def _query_pairs() -> list[tuple[Any, ...]]:
        params: dict[str, Any] = {
            "chain": chain,
            "lookback": max(lookback_hours, 1),
            "min_swaps": PND_MIN_PAIR_SWAPS,
            "limit": 50,
        }
        try:
            return cast(
                "list[tuple[Any, ...]]",
                client.execute(
                    f"""
                SELECT contract_address, token0, token1, count() AS swaps,
                       sum(toFloat64(amount0_in)) + sum(toFloat64(amount0_out)) AS vol0
                FROM {DEX_EVENTS_TABLE}
                WHERE chain = %(chain)s
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL %(lookback)s HOUR
                GROUP BY contract_address, token0, token1
                HAVING count() >= %(min_swaps)s
                ORDER BY vol0 DESC
                LIMIT %(limit)s
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                    params,
                    settings={"max_execution_time": MAX_EXECUTION_TIME},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("pump_dump pairs query failed for %s: %s", chain, exc)
            return []

    pairs = await run_sync(_query_pairs)

    for pair_row in pairs[:50]:
        try:
            pair_address, token0, token1 = pair_row[0], pair_row[1], pair_row[2]
            pair_address = str(pair_address).lower()
            token0 = str(token0 or "").lower()
            token1 = str(token1 or "").lower()
            token_address = token0 or pair_address

            # ── Step 2: volume windows (4h vs trailing 24h) ──
            vol_4h, vol_24h = await _query_pair_volume(client, chain, pair_address)
            if vol_24h <= 0 or vol_4h <= 0:
                continue

            baseline_4h = max(vol_24h - vol_4h, 0.0) / 5.0  # other 20h normalized to a 4h window
            spike_ratio = vol_4h / baseline_4h if baseline_4h > 0 else float("inf")
            volume_spike = spike_ratio > PND_VOLUME_SPIKE_MULT

            # ── Step 3: price series (reserve ratios per block) ──
            prices, first_ts, last_ts, peak_block = await _query_price_series(
                client, chain, pair_address, lookback_hours
            )
            if len(prices) < 2:
                continue

            baseline_price = _median(prices[: max(1, len(prices) // 5)])
            peak_price = max(prices)
            last_price = prices[-1]
            price_spike_pct = ((peak_price - baseline_price) / baseline_price * 100.0) if baseline_price > 0 else 0.0
            drawdown_pct = ((peak_price - last_price) / peak_price * 100.0) if peak_price > 0 else 0.0
            price_spike = price_spike_pct > PND_PRICE_SPIKE_PCT
            drawdown = drawdown_pct > PND_DRAWDOWN_PCT

            if not volume_spike:
                continue

            if volume_spike and price_spike and drawdown:
                confidence = "HIGH"
            elif volume_spike and price_spike:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            events.append(
                {
                    "attack_type": "pump_dump",
                    "confidence": confidence,
                    "chain": chain,
                    "block_number": int(peak_block or 0),
                    "tx_hashes": [],
                    "token_address": token_address,
                    "pair_address": pair_address,
                    "value_usd": None,
                    "attacker": "",
                    "evidence": [
                        f"vol spike {spike_ratio:.1f}x (4h {vol_4h:.4g} vs 24h {vol_24h:.4g})",
                        f"price spike {price_spike_pct:.0f}%",
                        f"drawdown {drawdown_pct:.0f}% from peak",
                        f"window {first_ts} → {last_ts}",
                    ],
                }
            )
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("pump_dump pair parse failed: %s", exc)
            continue

    events = await _enrich_usd(events[:MAX_EVENTS_PER_SCAN])
    _set_cached(cache_key, events)
    logger.info("attack_detector_pump_dump", chain=chain, count=len(events), lookback_hours=lookback_hours)
    return events


async def _query_pair_volume(client: Any, chain: str, pair_address: str) -> tuple[float, float]:
    """Return (vol_4h, vol_24h) for a pair using both token sides."""

    def _query() -> list[tuple[Any, ...]]:
        params: dict[str, Any] = {"chain": chain, "pair": pair_address}
        try:
            return cast(
                "list[tuple[Any, ...]]",
                client.execute(
                    f"""
                SELECT
                    sumIf(
                        toFloat64(amount0_in) + toFloat64(amount0_out)
                        + toFloat64(amount1_in) + toFloat64(amount1_out),
                        block_timestamp >= now() - INTERVAL 4 HOUR
                    ) AS vol_4h,
                    sum(
                        toFloat64(amount0_in) + toFloat64(amount0_out)
                        + toFloat64(amount1_in) + toFloat64(amount1_out)
                    ) AS vol_24h
                FROM {DEX_EVENTS_TABLE}
                WHERE chain = %(chain)s
                  AND contract_address = %(pair)s
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL 24 HOUR
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                    params,
                    settings={"max_execution_time": MAX_EXECUTION_TIME},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("pair volume query failed: %s", exc)
            return []

    rows = await run_sync(_query)
    if not rows or not rows[0]:
        return 0.0, 0.0
    try:
        return _to_float(rows[0][0]), _to_float(rows[0][1])
    except (ValueError, TypeError, IndexError):
        return 0.0, 0.0


async def _query_price_series(
    client: Any, chain: str, pair_address: str, lookback_hours: int
) -> tuple[list[float], str, str, int]:
    """Return (prices, first_ts, last_ts, peak_block) from reserve-ratio swaps.

    Price proxy: token1-per-token0 rate from swaps where amount0_in > 0
    (following the C6 spec's argMax price series).
    """

    def _query() -> list[tuple[Any, ...]]:
        params: dict[str, Any] = {"chain": chain, "pair": pair_address, "lookback": max(lookback_hours, 1)}
        try:
            return cast(
                "list[tuple[Any, ...]]",
                client.execute(
                    f"""
                SELECT block_number, block_timestamp,
                       argMax(
                           toFloat64(amount1_out) / NULLIF(toFloat64(amount0_in), 0),
                           block_number
                       ) AS price
                FROM {DEX_EVENTS_TABLE}
                WHERE chain = %(chain)s
                  AND contract_address = %(pair)s
                  AND type = 'swap'
                  AND amount0_in > 0
                  AND block_timestamp >= now() - INTERVAL %(lookback)s HOUR
                GROUP BY block_number, block_timestamp
                ORDER BY block_timestamp
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                    params,
                    settings={"max_execution_time": MAX_EXECUTION_TIME},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("price series query failed: %s", exc)
            return []

    rows = await run_sync(_query)
    prices: list[float] = []
    first_ts = ""
    last_ts = ""
    peak_block = 0
    peak_price = 0.0
    for row in rows:
        try:
            block_number = int(row[0])
            ts = str(row[1])
            price = _to_float(row[2])
            if price <= 0:
                continue
            if not first_ts:
                first_ts = ts
            last_ts = ts
            prices.append(price)
            if price > peak_price:
                peak_price = price
                peak_block = block_number
        except (ValueError, TypeError, IndexError):
            continue
    return prices, first_ts, last_ts, peak_block


# ═══════════════════════════════════════════════════════════════════════════
# 3. FLASH LOAN DETECTOR (v1 pattern-based)
# ═══════════════════════════════════════════════════════════════════════════


async def detect_flash_loans(chain: str, hours_back: int = 1, force_fresh: bool = False) -> list[dict[str, Any]]:
    """Detect flash-loan patterns from dex_events (same-tx multi-swap).

    v1 pattern: a tx_hash with >= 4 swap events where a single pair shows
    total in ≈ total out (repay) with the target swap in between.

    Args:
        chain: Chain name.
        hours_back: Lookback window in hours (default 1).
        force_fresh: Bypass the in-process cache.

    Returns:
        List of flash_loan event dicts.
    """
    chain = _normalize_chain(chain)
    cache_key = f"flash_loan:{chain}:{hours_back}"
    cached = _get_cached(cache_key)
    if not force_fresh and cached is not None:
        return cached

    events: list[dict[str, Any]] = []
    client = _get_ch_client()
    if client is None:
        return events

    def _query() -> list[tuple[Any, ...]]:
        params: dict[str, Any] = {"chain": chain, "hours": max(hours_back, 1), "limit": MAX_EVENTS_PER_SCAN}
        try:
            return cast(
                "list[tuple[Any, ...]]",
                client.execute(
                    f"""
                SELECT tx_hash, contract_address, token0, token1,
                       count() AS swap_count,
                       sum(toFloat64(amount0_in)) AS total_in,
                       sum(toFloat64(amount0_out)) AS total_out,
                       argMax(block_number, block_number) AS block_number,
                       argMax(block_timestamp, block_timestamp) AS block_timestamp
                FROM {DEX_EVENTS_TABLE}
                WHERE chain = %(chain)s
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
                  AND tx_hash IN (
                      SELECT tx_hash
                      FROM {DEX_EVENTS_TABLE}
                      WHERE chain = %(chain)s
                        AND type = 'swap'
                        AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
                      GROUP BY tx_hash
                      HAVING count() >= 4
                  )
                GROUP BY tx_hash, contract_address, token0, token1
                HAVING count() >= 2
                ORDER BY block_timestamp DESC
                LIMIT %(limit)s
                """,  # noqa: S608 - verified bound/allowlisted SQL construction  # nosec B608 - param-bound query; f-string only interpolates allowlisted literals/placeholders
                    params,
                    settings={"max_execution_time": MAX_EXECUTION_TIME},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("flash loan query failed for %s: %s", chain, exc)
            return []

    rows = await run_sync(_query)
    for row in rows:
        try:
            tx_hash, pair_address, token0, _token1 = row[0], row[1], row[2], row[3]
            swap_count = int(row[4])
            total_in = _to_float(row[5])
            total_out = _to_float(row[6])
            block_number = int(row[7])
            block_ts = row[8]

            denom = max(total_in, total_out)
            if denom <= 0:
                continue
            repay_diff = abs(total_in - total_out) / denom
            if repay_diff > FLASH_REPAY_TOLERANCE:
                continue

            if swap_count >= 4:
                confidence = "HIGH"
            elif swap_count >= 3:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"

            token_address = str(token0 or "").lower() or str(pair_address).lower()
            events.append(
                {
                    "attack_type": "flash_loan",
                    "confidence": confidence,
                    "chain": chain,
                    "block_number": block_number,
                    "tx_hashes": [str(tx_hash)],
                    "token_address": token_address,
                    "pair_address": str(pair_address).lower(),
                    "value_usd": None,
                    "raw_amount": total_out,  # token0 units flash-loaned
                    "attacker": "",
                    "evidence": [
                        f"{swap_count} swaps in one tx",
                        f"repay diff {repay_diff * 100:.1f}% (in {total_in:.6g} / out {total_out:.6g})",
                        f"block {block_ts}",
                    ],
                }
            )
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("flash loan row parse failed: %s", exc)
            continue

    events = await _enrich_usd(events[:MAX_EVENTS_PER_SCAN])
    _set_cached(cache_key, events)
    logger.info("attack_detector_flash_loan", chain=chain, count=len(events), hours=hours_back)
    return events


async def _enrich_usd(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Call-time delegate to the USD enrichment in ``attack_detector``.

    The enrichment (and its per-token price cache) lives in the orchestrator
    module; resolved at call time so tests patching
    ``attack_detector._estimate_usd`` keep working.
    """
    from rugmunchbot._vendor.scanner_engines.attack_detector import _enrich_usd as _real

    return await _real(events)


def _to_float(value: Any) -> float:
    """Safe float conversion (None → 0.0)."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _median(values: list[float]) -> float:
    """Median of a list of floats (0.0 for empty)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0
