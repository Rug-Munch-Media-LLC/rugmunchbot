"""trend_by_chain tool — per-chain trending with OUR transparent scoring formula.

This tool computes trending tokens PER CHAIN using a transparent, documented
scoring formula over OUR data where available, with DexScreener (and
optionally Birdeye) as the metric fallback.

Data cascade (own data first, external fallback):

  1. Own-data candidates
     - backend ``/api/v1/scanner/recent``          — our scanner activity
       (real risk scores, honeypot / rug-pull flags, chain, real symbol).
     - backend ``/api/v1/market/trending``         — our fused trending feed.
     - backend ``/api/v1/launches/recent``         — ClickHouse
       ``deployer_events`` (our own chain indexer) when rows exist.
  2. DexScreener ``/token-profiles/latest/v1``     — fresh launches per chain
     (real addresses + chain; used only as candidate seeds).
  3. Per-token metrics — DexScreener ``/latest/dex/search?q={address}``
     (24h volume, liquidity USD, buy/sell txs h24, pair age). This is the
     fallback metric layer (free, no key).
  4. Optional Birdeye ``token_overview`` holder counts (only when a
     ``BIRDEYE_API_KEY`` is present; silently degrades to None on
     rate-limit/errors — never blocks).

SCORING FORMULA (returned in the payload under ``scoring.formula`` so it is
fully auditable):

    score = 100 * ( w_vol * S_vol
                  + w_liq * S_liq
                  + w_growth * S_growth
                  + w_momentum * S_momentum
                  + w_age * S_age ) * (1 - risk_penalty)

    weights:  w_vol=0.25, w_liq=0.15, w_growth=0.15,
              w_momentum=0.20, w_age=0.25

    S_vol      = 1 - exp(-volume_24h / 1_000_000)          # $1M reference
    S_liq      = 1 - exp(-liquidity_usd / 250_000)          # $250k reference
    S_growth   = 0.5 * clamp01((buy_ratio - 0.40) / 0.60)   # buy pressure h24
               + 0.5 * (1 - exp(-(buys+sells) / 2_000))     # trade activity h24
    S_momentum = 1 - exp(-abs(price_change_24h) / 100)      # |%| mover — BOTH
                                                             # directions count
                                                             # (juicy rugs too)
    S_age      = 1 - min(age_hours / 72, 1)  (unknown age -> 0.5 neutral)

    risk_penalty (from OUR scanner; 0 when unscanned):
      honeypot / rug-pull                    -> 1.0  (excluded)
      risk_score>=50                         -> clamp01((score-50)/50)*0.60

Age and |mover| weights are deliberately high: fresh launches (pump.fun /
newly deployed) and the biggest % movers (up AND down) are what degens
trade. Volume/liquidity alone would bias toward established infra tokens
nobody trades. Only REAL symbols / addresses (from the sources above) are
ever returned. If a candidate has no resolvable pair or no real symbol it
is dropped — never invented.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Any

from app.domains.telegram.rugmunchbot.config import DEXSCREENER
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import http_get_any, http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.trend_algo")

# DexScreener token-profiles is NOT under /latest/dex — absolute URL.
DEXSCREENER_TOKEN_PROFILES = "https://api.dexscreener.com/token-profiles/latest/v1"  # noqa: S105 — public API URL (no secret)

# Backend route limits (validated by probe: scanner/recent le=100,
# market/trending le=50).
_SCANNER_RECENT_LIMIT = 100
_TRENDING_LIMIT = 50
_LAUNCHES_LIMIT = 80

# ── Transparent scoring constants (mirrored in FORMULA payload) ───────────
_VOL_REF_USD = 1_000_000.0
_LIQ_REF_USD = 250_000.0
_TRADES_REF = 2_000.0
_MOMENTUM_REF_PCT = 100.0  # |24h %| reference — a 100% move (either way)
_AGE_DECAY_HOURS = 72.0
_W_VOL = 0.25
_W_LIQ = 0.15
_W_GROWTH = 0.15
_W_MOMENTUM = 0.20
_W_AGE = 0.25
_RISK_FLOOR = 50.0
_RISK_MAX_PENALTY = 0.60

_FORMULA: dict[str, Any] = {
    "score": "100 * (w_vol*S_vol + w_liq*S_liq + w_growth*S_growth + w_momentum*S_momentum + w_age*S_age) * (1 - risk_penalty)",
    "weights": {
        "volume_24h": _W_VOL,
        "liquidity": _W_LIQ,
        "holder_growth": _W_GROWTH,
        "momentum_24h": _W_MOMENTUM,
        "age": _W_AGE,
    },
    "S_vol": "1 - exp(-volume_24h / 1_000_000)",
    "S_liq": "1 - exp(-liquidity_usd / 250_000)",
    "S_growth": "0.5*clamp01((buy_ratio-0.40)/0.60) + 0.5*(1 - exp(-(buys+sells)/2_000))",
    "S_momentum": "1 - exp(-abs(price_change_24h) / 100)  # |%| mover, up AND down",
    "S_age": "1 - min(age_hours/72, 1)  (unknown -> 0.5)",
    "risk_penalty": "1.0 if honeypot/rug-pull else clamp01((risk_score-50)/50)*0.60 (0 if unscanned)",
    "note": (
        "holder_growth is proxied by h24 buy pressure + trade activity; "
        "momentum_24h rewards the biggest |%| movers in both directions "
        "(green pumps AND red rugs) — fresh launches and movers outrank "
        "established infra tokens that nobody trades."
    ),
}

# DexScreener chain id normalisation (aliases -> canonical DexScreener id)
_CHAIN_ALIASES: dict[str, str] = {
    "eth": "ethereum",
    "ethereum": "ethereum",
    "0x1": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "binance-smart-chain": "bsc",
    "arb": "arbitrum",
    "arbitrum": "arbitrum",
    "arbitrum-one": "arbitrum",
    "base": "base",
    "polygon": "polygon",
    "matic": "polygon",
    "polygon-pos": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avax": "avalanche",
    "avalanche": "avalanche",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "robinhood": "robinhood",
    "ton": "ton",
    "tron": "tron",
    "trx": "tron",
    "zksync": "zksync",
    "polygon_zkevm": "polygon_zkevm",
    "scroll": "scroll",
    "mantle": "mantle",
    "sei": "sei",
    "sui": "sui",
    "aptos": "aptos",
    "injective": "injective",
    "berachain": "berachain",
    "bera": "berachain",
    "hyperliquid": "hyperliquid",
    "mode": "mode",
    "fantom": "fantom",
    "ftm": "fantom",
}

_METRIC_FETCH_CONCURRENCY = 10
_MAX_CANDIDATES = 60
_METRIC_FETCH_DEADLINE = 10.0  # seconds; partial results still return


def _normalize_chain(chain: str) -> str:
    """Return canonical DexScreener chain id, or the raw value if unknown."""
    key = chain.strip().lower()
    if key in ("", "all", "any", "multi"):
        return "all"
    return _CHAIN_ALIASES.get(key, key)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _exp_norm(value: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    return 1.0 - math.exp(-max(value, 0.0) / reference)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# ── Candidate collection (own data first) ─────────────────────────────────


async def _scanner_candidates() -> list[dict[str, Any]]:
    """OUR scanner activity: real risk scores + honeypot flags per token."""
    body = await backend_json_get("/api/v1/scanner/recent", params={"limit": _SCANNER_RECENT_LIMIT}, timeout=8.0)
    if not isinstance(body, list):
        return []
    out: list[dict[str, Any]] = []
    for it in body:
        if not isinstance(it, dict):
            continue
        addr = it.get("token_address") or it.get("contract_address") or ""
        if not addr:
            continue
        chain = _normalize_chain(str(it.get("chain") or ""))
        out.append(
            {
                "address": addr,
                "chain": chain,
                "symbol": (it.get("token_symbol") or "").strip(),
                "risk_score": _to_float(it.get("risk_score")),
                "is_honeypot": bool(it.get("is_honeypot")),
                "is_rug_pull": bool(it.get("is_rug_pull")),
                "scanned_at": it.get("scanned_at"),
                "source": "own:scanner/recent",
            }
        )
    return out


async def _trending_candidates() -> list[dict[str, Any]]:
    """Our fused trending feed — real symbols/addresses/chain."""
    body = await backend_json_get("/api/v1/market/trending", params={"limit": _TRENDING_LIMIT}, timeout=8.0)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        addr = it.get("address") or it.get("token_address") or ""
        if not addr:
            continue
        chain = _normalize_chain(str(it.get("chain") or ""))
        if chain == "all":
            continue
        out.append(
            {
                "address": addr,
                "chain": chain,
                "symbol": (it.get("symbol") or "").strip(),
                "source": "own:market/trending",
            }
        )
    return out


async def _launch_candidates() -> list[dict[str, Any]]:
    """ClickHouse deployer_events via our launch feed (own indexer data)."""
    body = await backend_json_get("/api/v1/launches/recent", params={"limit": _LAUNCHES_LIMIT}, timeout=8.0)
    items = body.get("items") if isinstance(body, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        # Only our own ClickHouse rows — DexScreener boost rows carry no
        # symbol and no launch timestamp, so they add no scoring signal.
        if it.get("source") != "own_clickhouse":
            continue
        addr = it.get("token_address") or ""
        if not addr:
            continue
        chain = _normalize_chain(str(it.get("chain") or ""))
        out.append(
            {
                "address": addr,
                "chain": chain,
                "symbol": (it.get("symbol") or "").strip(),
                "launch_timestamp": it.get("launch_timestamp"),
                "source": "own:clickhouse:deployer_events",
            }
        )
    return out


async def _dex_profile_candidates() -> list[dict[str, Any]]:
    """DexScreener fresh-launch profiles (real addresses + chain seeds)."""
    body = await http_get_any(DEXSCREENER_TOKEN_PROFILES, timeout=8.0)
    if not isinstance(body, list):
        return []
    out: list[dict[str, Any]] = []
    for it in body:
        if not isinstance(it, dict):
            continue
        addr = it.get("tokenAddress") or ""
        if not addr:
            continue
        chain = _normalize_chain(str(it.get("chainId") or ""))
        out.append({"address": addr, "chain": chain, "symbol": "", "source": "dexscreener:token-profiles"})
    return out


# ── Per-token metrics (DexScreener search; Birdeye optional) ──────────────


async def _fetch_pair_metrics(address: str) -> dict[str, Any] | None:
    """Real per-token metrics from DexScreener. None when unresolvable.

    Returns the HIGHEST-24h-volume pair whose baseToken matches ``address``.
    ``symbol`` is the real DexScreener baseToken symbol — never synthesised.
    """
    body = await http_get_json(f"{DEXSCREENER}/search", params={"q": address}, timeout=8.0)
    if not isinstance(body, dict):
        return None
    pairs = body.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        return None
    best: dict[str, Any] | None = None
    best_vol = -1.0
    for p in pairs:
        if not isinstance(p, dict):
            continue
        bt = p.get("baseToken")
        if not isinstance(bt, dict) or bt.get("address") != address:
            continue
        symbol = (bt.get("symbol") or "").strip()
        if not symbol:
            continue
        vol = _to_float((p.get("volume") or {}).get("h24") if isinstance(p.get("volume"), dict) else None) or 0.0
        if vol > best_vol:
            best_vol = vol
            best = p
    if best is None:
        return None
    bt = best.get("baseToken") or {}
    volume = best.get("volume") or {}
    price_change = best.get("priceChange") or {}
    liquidity = best.get("liquidity") or {}
    txns = best.get("txns") or {}
    h24_txns = txns.get("h24") if isinstance(txns, dict) else None
    created_ms = _to_float(best.get("pairCreatedAt"))
    age_hours = max((_time_ms_now() - created_ms) / 3_600_000.0, 0.0) if created_ms else None
    return {
        "address": address,
        "chain": _normalize_chain(str(best.get("chainId") or "")),
        "symbol": (bt.get("symbol") or "").strip().upper(),
        "name": (bt.get("name") or "").strip(),
        "dex_id": str(best.get("dexId") or ""),
        "price_usd": _to_float(best.get("priceUsd")),
        "volume_24h": _to_float(volume.get("h24")),
        "liquidity_usd": _to_float(liquidity.get("usd")),
        "price_change_24h": _to_float(price_change.get("h24")),
        "buys_24h": _to_int(h24_txns.get("buys") if isinstance(h24_txns, dict) else None),
        "sells_24h": _to_int(h24_txns.get("sells") if isinstance(h24_txns, dict) else None),
        "age_hours": age_hours,
        "url": str(best.get("url") or ""),
    }


def _time_ms_now() -> float:
    import time

    return time.time() * 1000.0


async def _birdeye_holders(address: str, chain: str) -> int | None:
    """Optional holder count from Birdeye. Never blocks on failure."""
    key = os.getenv("BIRDEYE_API_KEY", "").strip()
    if not key:
        return None
    try:
        body = await http_get_json(
            "https://public-api.birdeye.so/defi/token_overview",
            params={"address": address},
            headers={"X-API-KEY": key, "x-chain": chain},
            timeout=5.0,
        )
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict):
            return None
        return _to_int(data.get("holder"))
    except Exception:  # pragma: no cover — defensive, never blocks
        return None


# ── Scoring ───────────────────────────────────────────────────────────────


def _score_token(metrics: dict[str, Any], risk: dict[str, Any] | None) -> dict[str, Any]:
    """Compute the transparent per-token score + breakdown."""
    volume_24h = metrics.get("volume_24h") or 0.0
    liquidity_usd = metrics.get("liquidity_usd") or 0.0
    buys = metrics.get("buys_24h") or 0
    sells = metrics.get("sells_24h") or 0
    total = float(buys + sells)
    buy_ratio = (buys / total) if total > 0 else 0.5
    price_change_24h = metrics.get("price_change_24h") or 0.0

    s_vol = _exp_norm(float(volume_24h), _VOL_REF_USD)
    s_liq = _exp_norm(float(liquidity_usd), _LIQ_REF_USD)
    s_growth = 0.5 * _clamp01((buy_ratio - 0.40) / 0.60) + 0.5 * _exp_norm(total, _TRADES_REF)
    s_momentum = _exp_norm(abs(float(price_change_24h)), _MOMENTUM_REF_PCT)
    age_h = metrics.get("age_hours")
    s_age = (1.0 - min(max(float(age_h), 0.0) / _AGE_DECAY_HOURS, 1.0)) if age_h is not None else 0.5

    if risk is not None and (risk.get("is_honeypot") or risk.get("is_rug_pull")):
        risk_penalty = 1.0
    elif risk is not None and risk.get("risk_score") is not None:
        risk_score = float(risk["risk_score"])
        risk_penalty = _clamp01((risk_score - _RISK_FLOOR) / (100.0 - _RISK_FLOOR)) * _RISK_MAX_PENALTY
    else:
        risk_penalty = 0.0

    raw = _W_VOL * s_vol + _W_LIQ * s_liq + _W_GROWTH * s_growth + _W_MOMENTUM * s_momentum + _W_AGE * s_age
    final = round(100.0 * raw * (1.0 - risk_penalty), 1)

    return {
        "score": final,
        "components": {
            "volume_score": round(s_vol * 100.0, 1),
            "liquidity_score": round(s_liq * 100.0, 1),
            "growth_score": round(s_growth * 100.0, 1),
            "momentum_score": round(s_momentum * 100.0, 1),
            "age_score": round(s_age * 100.0, 1),
        },
        "risk_penalty": round(risk_penalty * 100.0, 1),
    }


def _sketch_score(metrics: dict[str, Any], risk: dict[str, Any] | None) -> dict[str, Any]:
    """Risk-first sketchiness score (rank="risk") — HIGHER = sketchier.

    Unlike the trend ``score`` (momentum-weighted, flagged tokens EXCLUDED),
    the sketch score ranks by how risky/sketchy a token looks:

      * +100  — our scanner flagged honeypot / rug-pull (the sketchiest).
      * +0-100 — our scanner's ``risk_score`` when present.
      * +0-25  — low liquidity proxy (thin exit).
      * +0-20  — violent |24h %| move (pump or dump).
      * +0-15  — fresh launch (younger than the 72h age reference).

    Unscanned tokens still get the liquidity/momentum/age proxies, so the
    tool never returns an empty list just because our scanner has not seen
    the tokens yet. Returns the score + a human-readable ``flags`` list the
    LLM can cite as the "why" for each pick.
    """
    score = 0.0
    flags: list[str] = []
    flagged = bool(risk and (risk.get("is_honeypot") or risk.get("is_rug_pull")))
    if risk is not None and flagged:
        score += 100.0
        if risk.get("is_honeypot"):
            flags.append("honeypot")
        if risk.get("is_rug_pull"):
            flags.append("rug_pull")
    if risk is not None and risk.get("risk_score") is not None:
        r = float(risk["risk_score"])
        score += r
        if r >= 70.0:
            flags.append(f"risk_score={r:.0f}/100")
    if risk is None:
        flags.append("unscanned")
    liquidity = metrics.get("liquidity_usd") or 0.0
    score += (1.0 - _exp_norm(float(liquidity), _LIQ_REF_USD)) * 25.0
    if liquidity < 5_000.0:
        flags.append("thin_liquidity")
    price_change = abs(float(metrics.get("price_change_24h") or 0.0))
    score += _exp_norm(price_change, _MOMENTUM_REF_PCT) * 20.0
    if price_change >= 50.0:
        flags.append("violent_moves")
    age_h = metrics.get("age_hours")
    if age_h is not None:
        score += (1.0 - min(max(float(age_h), 0.0) / _AGE_DECAY_HOURS, 1.0)) * 15.0
        if float(age_h) < _AGE_DECAY_HOURS:
            flags.append("fresh_launch")
    if not flags:
        flags.append("sketchy_proxies")
    return {"sketch_score": round(min(score, 200.0), 1), "flags": flags}


# ── Tool handler ──────────────────────────────────────────────────────────


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
    risk_by_key: dict[tuple[str, str], dict[str, Any]] = {(c["chain"], c["address"].lower()): c for c in scanner}

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
