"""pattern_memory — Redis-backed "patterns seen before" recall for RugMunchBot.

A lightweight recall store so the bot can answer *"have we seen this pattern
before?"* with real history instead of a guess. When a scan detects a pattern
(e.g. pump-and-dump, honeypot, liquidity rug) the scanner pipeline calls
:func:`remember_pattern` to persist it; when the user asks about a new token
the :func:`pattern_recall` tool fuzzy-matches the token's scan signals against
every stored pattern and returns the similar ones with their historical
outcomes.

Design
------
* **Storage**: a single bounded Redis HASH ``bot:pattern_memory:patterns``
  (pattern_id -> JSON record). ``pattern_id`` is stable per
  (token, pattern, epoch-ms) so re-detecting the same pattern replaces the old
  record. The store is capped at ``_MAX_PATTERNS``; the oldest records are
  pruned when the cap is exceeded (bounded memory, no per-field TTL needed).
* **Fuzzy match**: :func:`_similarity` compares the numeric ``signals`` dicts
  on their *intersection* of keys using relative error — mean of
  ``1 - |a-b|/max(|a|,|b|)`` per key. Deterministic, cheap, unit-testable.
  A stored pattern whose signals have no keys in common scores 0.0.
* **Zero fabrication**: :func:`pattern_recall` ONLY returns records that are
  actually in Redis. When nothing is similar it returns ``matches: []`` with
  an honest "no similar patterns in memory" summary — never an invented
  history. The same applies when a token resolves but no numeric signals can
  be derived from the real DexScreener snapshot.
* **Fail-open**: every Redis call is best-effort and never raises (same
  contract as ``channels/scheduler_state`` / ``channels/scam_watch``). When
  Redis is down :func:`remember_pattern` returns ``False`` and
  :func:`pattern_recall`` returns ``ok=False`` with the honest
  "pattern memory unavailable" error.
* **Signal derivation**: when the caller passes no explicit ``signals`` the
  tool derives them from a REAL DexScreener pair snapshot (``change_24h`` /
  ``change_6h`` / ``change_1h`` / ``volume_24h`` / ``liquidity_usd``). If no
  pair resolves the tool says so — numbers are never invented.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from rugmunchbot._vendor.redis_client import get_redis_async
from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools._pattern_scoring import (
    _clean_signals,
    _similarity,
    _to_float,
    clamp_params,
    empty_recall_result,
    recall_summary,
    score_patterns,
)
from rugmunchbot.tools.registry import ToolResult

logger = get_logger("rmi_bot.tools.pattern_memory")

# Re-exported for callers/tests that score signals through this module.
__all__ = ["_clean_signals", "_similarity", "_to_float", "pattern_recall", "remember_pattern"]

# Redis namespace (shared "bot:" prefix with scam_watch / scheduler_state).
_PATTERNS_HASH = "bot:pattern_memory:patterns"

# Bounded store: at most this many records survive; oldest are pruned.
_MAX_PATTERNS = 2000

# Default recall knobs (top-N + similarity floor).
_DEFAULT_TOP_K = 5
_DEFAULT_MIN_SIMILARITY = 0.5

# Numeric snapshot keys derived from a real DexScreener pair snapshot when the
# caller did not pass explicit scan signals.
_SNAPSHOT_SIGNAL_KEYS = ("change_24h", "change_6h", "change_1h", "volume_24h", "liquidity_usd")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── Redis layer (fail-open, never raises) ─────────────────────────────────


async def _all_patterns() -> list[dict[str, Any]] | None:
    """All stored pattern records (newest first), or None when Redis is down."""
    r = get_redis_async()
    if r is None:
        return None
    try:
        raw = await r.hgetall(_PATTERNS_HASH)
    except Exception as exc:  # fail-open by contract
        logger.debug("pattern_memory:read_failed err=%s", str(exc)[:120])
        return None
    records: list[dict[str, Any]] = []
    for value in (raw or {}).values():
        if not isinstance(value, str):
            continue
        try:
            rec = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(rec, dict):
            records.append(rec)
    records.sort(key=lambda rec: str(rec.get("detected_at") or ""), reverse=True)
    return records


async def _prune_oldest(remove_n: int) -> None:
    """Best-effort: delete the oldest ``remove_n`` records (by detected_at)."""
    if remove_n <= 0:
        return
    r = get_redis_async()
    if r is None:
        return
    raw = await r.hgetall(_PATTERNS_HASH)
    ids_by_time: list[tuple[str, str]] = []
    for pid, value in (raw or {}).items():
        try:
            rec = json.loads(value) if isinstance(value, str) else {}
        except (json.JSONDecodeError, TypeError):
            rec = {}
        ids_by_time.append((str(pid), str(rec.get("detected_at") or "")))
    ids_by_time.sort(key=lambda pair: pair[1])
    for pid, _ in ids_by_time[:remove_n]:
        await r.hdel(_PATTERNS_HASH, pid)


async def _store_record(record: dict[str, Any]) -> bool:
    """Persist one record under a stable id; prune oldest when over the cap."""
    r = get_redis_async()
    if r is None:
        return False
    token = str(record.get("token") or "unknown").lower()
    pattern = str(record.get("pattern") or "unknown")
    pattern_id = f"{token}:{pattern}:{int(time.time() * 1000)}"
    try:
        await r.hset(_PATTERNS_HASH, pattern_id, json.dumps(record, default=str))
        try:
            count = int(await r.hlen(_PATTERNS_HASH) or 0)
            if count > _MAX_PATTERNS:
                await _prune_oldest(count - _MAX_PATTERNS)
        except Exception as exc:  # pruning is best-effort
            logger.debug("pattern_memory:prune_failed err=%s", str(exc)[:120])
        return True
    except Exception as exc:  # fail-open by contract
        logger.debug("pattern_memory:write_failed err=%s", str(exc)[:120])
        return False


async def remember_pattern(
    *,
    token: str,
    pattern: str,
    signals: dict[str, float] | None = None,
    outcome: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Store a detected pattern for later recall. Best-effort, never raises.

    Args:
        token: Token address or ``$SYMBOL`` the pattern was detected on.
        pattern: Short pattern name (e.g. "pump-and-dump", "honeypot",
            "liquidity-rug").
        signals: Numeric scan signals that DEFINE the pattern (e.g.
            ``{"change_24h": 200.0, "change_48h": -85.0}``). Fuzzy recall
            matches on these; only finite numbers are stored.
        outcome: Historical result once known (e.g.
            ``{"label": "rug", "note": "LP removed"}``).
        metadata: Free-form context (chain, dex, scan id, ...).

    Returns:
        True when persisted to Redis; False when Redis is down or the record
        is invalid (no token or no pattern name).
    """
    record: dict[str, Any] = {
        "token": (token or "").strip().lower(),
        "pattern": (pattern or "").strip(),
        "signals": _clean_signals(signals),
        "outcome": outcome if isinstance(outcome, dict) else None,
        "metadata": metadata if isinstance(metadata, dict) else None,
        "detected_at": _now_iso(),
    }
    if not record["token"] or not record["pattern"]:
        return False
    return await _store_record(record)


# ── Real signal derivation (DexScreener snapshot; never synthesized) ──────


async def _snapshot_signals(address: str, symbol: str) -> dict[str, float] | None:
    """Derive numeric scan signals from a REAL DexScreener pair snapshot.

    Returns None when no pair can be resolved. Only real snapshot numbers are
    used — no synthesis. The best pair is the highest-24h-volume one.
    """
    from rugmunchbot.config import DEXSCREENER
    from rugmunchbot.tools._client import http_get_json

    if address:
        body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
        pairs = (body or {}).get("pairs") if isinstance(body, dict) else None
    elif symbol:
        q = symbol.strip().upper()
        if not q:
            return None
        body = await http_get_json(f"{DEXSCREENER}/search", params={"q": q}, timeout=6.0)
        pairs = (body or {}).get("pairs") if isinstance(body, dict) else None
    else:
        return None
    if not isinstance(pairs, list) or not pairs:
        return None
    best = max(
        (p for p in pairs if isinstance(p, dict)),
        key=lambda p: _to_float((p.get("volume") or {}).get("h24")) or 0.0,
        default=None,
    )
    if not best:
        return None

    def _as_dict(value: Any) -> dict[str, Any]:
        """Real dict sub-payloads from the snapshot (else empty)."""
        return value if isinstance(value, dict) else {}

    change = _as_dict(best.get("priceChange"))
    volume = _as_dict(best.get("volume"))
    liquidity = _as_dict(best.get("liquidity"))
    out: dict[str, float] = {}
    for key, source_key in zip(
        _SNAPSHOT_SIGNAL_KEYS,
        ("h24", "h6", "h1", "h24", "usd"),
        strict=False,
    ):
        if key.startswith("change"):
            source = change
        elif key.startswith("volume"):
            source = volume
        else:
            source = liquidity
        f = _to_float(source.get(source_key))
        if f is not None and math.isfinite(f):
            out[key] = f
    return out or None


# ── Tool handler ──────────────────────────────────────────────────────────


@dataclass
class PatternRecallQuery:
    address: str = ""
    symbol: str = ""
    chain: str = ""
    signals: dict[str, float] | None = None
    top_k: int = _DEFAULT_TOP_K
    min_similarity: float = _DEFAULT_MIN_SIMILARITY


async def pattern_recall(query: PatternRecallQuery | None = None, **overrides: Any) -> ToolResult:
    """Recall similar patterns seen before for a token's scan signals.

    Args live on ``query`` (a :class:`PatternRecallQuery`); ``overrides`` are
    applied on top for tool-registry compatibility.

    Fuzzy-matches explicit ``signals`` (or a REAL DexScreener snapshot derived
    from ``address``/``symbol``) against every pattern stored in Redis and
    returns the top matches with their historical outcomes plus an outcome
    distribution. Zero fabrication: only stored patterns are returned, and an
    empty match list is a real answer, never an invented history. ``ok=False``
    only when Redis is down or there is no token / no comparable signals.
    """
    query = query or PatternRecallQuery()
    for k, v in overrides.items():
        if not hasattr(query, k):
            raise TypeError(f"unknown pattern_recall option: {k}")
        setattr(query, k, v)
    address = query.address
    symbol = query.symbol
    chain = query.chain
    patterns = await _all_patterns()
    if patterns is None:
        return ToolResult(
            tool="pattern_recall",
            ok=False,
            error="pattern memory unavailable (Redis down)",
        )

    signals = _clean_signals(query.signals)
    snapshot: dict[str, Any] = {}
    if not signals and (address or symbol):
        snapshot_signals = await _snapshot_signals(address, symbol)
        if snapshot_signals:
            signals.update(snapshot_signals)
            if symbol:
                snapshot["symbol"] = symbol.strip().upper()
            else:
                snapshot["address"] = address
    if not signals:
        if not (address or symbol):
            return ToolResult(
                tool="pattern_recall",
                ok=False,
                error=(
                    "need a token address/symbol or explicit signals to recall similar patterns"
                ),
            )
        # Token resolved but no numeric signals derivable -> honest empty recall.
        return empty_recall_result(address, symbol, chain, snapshot, len(patterns))

    top_k_i, min_sim = clamp_params(query.top_k, query.min_similarity)
    matches, outcome_counts = score_patterns(patterns, signals, top_k_i, min_sim)
    return ToolResult(
        tool="pattern_recall",
        ok=True,
        data={
            "query": {"address": address, "symbol": symbol, "chain": chain},
            "store_available": True,
            "stored_count": len(patterns),
            "matches": matches,
            "outcome_distribution": outcome_counts,
            "summary_text": recall_summary(matches, outcome_counts, len(patterns)),
        },
        symbols=[snapshot["symbol"]] if snapshot.get("symbol") else [],
        addresses=[snapshot["address"]] if snapshot.get("address") else [],
        source="pattern_memory:redis",
    )


# ── Registration (self-owned; invoked by tools/__init__._register_defaults) ─


def register_tool(reg: Any) -> None:
    """Register the pattern_recall tool on the shared registry (see ta for notes)."""
    reg.register("pattern_recall", pattern_recall, timeout=8.0, ttl=30.0)
