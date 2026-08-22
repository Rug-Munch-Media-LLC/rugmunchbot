"""trend_by_chain tool handler — per-chain trending with transparent scoring.

Extracted verbatim from the former single-module ``tools/trend_algo.py``.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from rugmunchbot.support.logging import get_logger
from rugmunchbot.tools.registry import ToolResult
from rugmunchbot.tools.trend_algo._candidates import (
    _dex_profile_candidates,
    _launch_candidates,
    _scanner_candidates,
    _trending_candidates,
)
from rugmunchbot.tools.trend_algo._constants import (
    _FORMULA,
    _MAX_CANDIDATES,
    _METRIC_FETCH_CONCURRENCY,
    _METRIC_FETCH_DEADLINE,
)
from rugmunchbot.tools.trend_algo._metrics import _birdeye_holders, _fetch_pair_metrics
from rugmunchbot.tools.trend_algo._normalize import _normalize_chain
from rugmunchbot.tools.trend_algo._scoring import _score_token, _sketch_score

logger = get_logger("rmi_bot.tools.trend_algo")


async def trend_by_chain(*, chain: str = "all", limit: int = 10, rank: str = "trend") -> ToolResult:
    """Trending tokens per chain with OUR transparent scoring formula.

    Args:
        chain: canonical chain name ("solana", "ethereum", "base", "bsc",
            "arbitrum", ...) or "all" for a cross-chain ranked list.
        limit: max tokens to return.
        rank: "trend" (default) — momentum/volume score, flagged
            honeypot/rug-pull tokens EXCLUDED. "risk" — the riskiest /
            sketchiest picks: our scanner's flagged honeypots/rug-pulls are
            KEPT and ranked first (sketch_score), so a "riskiest of the day"
            question answers with actual risk, not % change.

    Returns:
        ToolResult with ``tokens`` (ranked, each with score_breakdown /
        sketch_score + risk_flags, and chain), ``scoring`` (the full
        formula) and real symbols/addresses.
    """
    want = _normalize_chain(chain)
    want_risk = rank == "risk"
    try:
        limit_i = max(1, min(int(limit), 50))
    except (TypeError, ValueError):
        limit_i = 10

    scanner, trending, launches, profiles = await asyncio.gather(
        _scanner_candidates(),
        _trending_candidates(),
        _launch_candidates(),
        _dex_profile_candidates(),
    )

    # Deduplicate candidate addresses per chain (own data takes precedence
    # for risk overlay later, so keep scanner rows first).
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in (scanner, trending, launches, profiles):
        for c in group:
            key = (c["chain"], c["address"].lower())
            if key in seen:
                continue
            seen.add(key)
            candidates.append(c)
    if want != "all":
        candidates = [c for c in candidates if c["chain"] == want]
    candidates = candidates[:_MAX_CANDIDATES]
    if not candidates:
        return ToolResult(
            tool="trend_by_chain",
            ok=False,
            error=f"no candidate tokens for chain={want} (sources unreachable or empty)",
        )

    # Fetch real metrics concurrently (DexScreener; Birdeye optional).
    # Bounded by a hard deadline so the tool never eats the full registry
    # timeout: whatever scored in time is returned (still real data).
    sem = asyncio.Semaphore(_METRIC_FETCH_CONCURRENCY)

    async def fetch_one(idx: int, cand: dict[str, Any]) -> tuple[int, dict[str, Any] | None]:
        """Fetch pair metrics for one candidate; returns (idx, metrics-or-None)."""
        async with sem:
            return idx, await _fetch_pair_metrics(cand["address"])

    tasks = [asyncio.ensure_future(fetch_one(i, c)) for i, c in enumerate(candidates)]
    done, pending = await asyncio.wait(tasks, timeout=_METRIC_FETCH_DEADLINE)
    for t in pending:
        t.cancel()
    if pending:
        logger.warning(
            "trend_by_chain metric fetch hit deadline (%ss) — %d/%d completed, continuing with partial results",
            _METRIC_FETCH_DEADLINE,
            len(done),
            len(candidates),
        )
    metric_results: list[dict[str, Any] | None] = [None] * len(candidates)
    for t in done:
        try:
            idx, metrics = t.result()
            metric_results[idx] = metrics
        except Exception as exc:  # pragma: no cover — per-call failures already swallowed
            logger.debug("trend_by_chain metric task failed: %s", exc)
            continue
    risk_by_key: dict[tuple[str, str], dict[str, Any]] = {
        (c["chain"], c["address"].lower()): c for c in scanner
    }

    tokens: list[dict[str, Any]] = []
    sources_used: set[str] = set()
    for cand, metrics in zip(candidates, metric_results, strict=False):
        if metrics is None:
            continue
        symbol = metrics.get("symbol") or cand.get("symbol") or ""
        if not symbol:
            continue  # never invent a symbol — drop candidates without one
        sources_used.add(cand["source"])
        risk = risk_by_key.get((cand["chain"], cand["address"].lower()))
        breakdown = _score_token(metrics, risk)
        if not want_risk and breakdown["risk_penalty"] >= 100.0:
            continue  # trend mode: our scanner flagged honeypot / rug-pull — exclude
        tok: dict[str, Any] = {
            "rank": 0,  # set below after sort
            "symbol": symbol,
            "name": metrics.get("name") or "",
            "address": cand["address"],
            "chain": cand["chain"],
            "price_usd": metrics.get("price_usd"),
            "volume_24h": metrics.get("volume_24h"),
            "liquidity_usd": metrics.get("liquidity_usd"),
            "price_change_24h": metrics.get("price_change_24h"),
            "buys_24h": metrics.get("buys_24h"),
            "sells_24h": metrics.get("sells_24h"),
            "buy_ratio_24h": round(
                (float(metrics.get("buys_24h") or 0))
                / max(float((metrics.get("buys_24h") or 0) + (metrics.get("sells_24h") or 0)), 1.0),
                3,
            ),
            "age_hours": metrics.get("age_hours"),
            "holders": None,  # enriched concurrently below (top tokens only)
            "risk_score": risk.get("risk_score") if risk else None,
            "risk_source": "our_scanner" if risk else "unscanned",
            "score": breakdown["score"],
            "score_breakdown": breakdown,
            "dex": metrics.get("dex_id"),
            "url": metrics.get("url") or "",
        }
        if want_risk:
            sketch = _sketch_score(metrics, risk)
            tok["sketch_score"] = sketch["sketch_score"]
            tok["risk_flags"] = sketch["flags"]
        tokens.append(tok)

    if want_risk:
        tokens.sort(key=lambda t: float(t["sketch_score"]), reverse=True)
    else:
        tokens.sort(key=lambda t: float(t["score"]), reverse=True)
    tokens = tokens[:limit_i]

    # Optional holder enrichment (Birdeye) — concurrent, deadline-bounded,
    # with a breaker: once Birdeye errors (e.g. rate-limit), skip the rest.
    solana_tokens = [t for t in tokens if t["chain"] == "solana"]
    if solana_tokens and os.getenv("BIRDEYE_API_KEY", "").strip():
        holder_sem = asyncio.Semaphore(4)

        async def holder_one(tok: dict[str, Any]) -> None:
            """Fetch Birdeye holder count for one Solana token (best-effort)."""
            async with holder_sem:
                tok["holders"] = await _birdeye_holders(tok["address"], tok["chain"])

        holder_tasks = [asyncio.ensure_future(holder_one(tok)) for tok in solana_tokens]
        _done_h, pending_h = await asyncio.wait(holder_tasks, timeout=4.0)
        for task in pending_h:
            task.cancel()

    for i, tok in enumerate(tokens, start=1):
        tok["rank"] = i

    if not tokens:
        return ToolResult(
            tool="trend_by_chain",
            ok=False,
            error=f"no scoreable tokens for chain={want} (metrics unresolvable)",
        )

    by_chain: dict[str, list[str]] = {}
    for tok in tokens:
        by_chain.setdefault(tok["chain"], []).append(tok["symbol"])

    return ToolResult(
        tool="trend_by_chain",
        ok=True,
        data={
            "chain": want,
            "limit": limit_i,
            "count": len(tokens),
            "rank_mode": "risk" if want_risk else "trend",
            "tokens": tokens,
            "by_chain": by_chain,
            "scoring": _FORMULA,
            "updated_at": _iso_now(),
        },
        symbols=[t["symbol"] for t in tokens],
        addresses=[t["address"] for t in tokens],
        source=", ".join(sorted(sources_used)) if sources_used else "dexscreener:search",
    )


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


# ── Registration (self-owned; invoked by tools/__init__._register_defaults) ─


def register_tool(reg: Any) -> None:
    """Register this tool on the shared registry.

    Timeout/TTL are owned here so the module controls its own cache/run
    budget; ``tools/__init__._register_defaults()`` only calls this.
    """
    reg.register("trend_by_chain", trend_by_chain, timeout=15.0, ttl=180.0)
