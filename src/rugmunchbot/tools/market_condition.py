"""market_condition tool — a synthesized macro read of "how's the market right now".

Answers "how's the market?", "is the market bullish or bearish?", "market
sentiment", "risk on/off", "is now a good time to buy/enter" with a
deterministic, fully-auditable verdict built ONLY from real numbers:

  * global Fear & Greed (backend -> alternative.me),
  * BTC / ETH 24h momentum (backend -> CoinGecko /coins/markets fallback),
  * global market cap + 24h change (CoinGecko /global),
  * top-chain TVL trend, 7d (DefiLlama /v2/chains + /v2/historicalChainTvl),
  * OUR trending breadth — pumping vs dumping in the backend trending feed.

The verdict (risk-on / risk-off / neutral), score and reasons are produced by
the pure :func:`assess_market_condition` — no LLM, no gut-feel, no fabricated
numbers. A signal whose source is down is simply ABSENT from the payload and
excluded from the score (real-data-only degradation). ``ok=False`` only when
every source is unreachable.

Sources probed live 2026-08-14 (real response shapes verified):
  * backend  /api/v1/databus/market/prices   -> {"prices": {"bitcoin": {"usd", "usd_market_cap", "usd_24h_change"}, ...}}
  * backend  /api/v1/databus/market/fear-greed -> {"value": 29, "classification": "Fear", ...}
  * backend  /api/v1/market/trending         -> {"items": [{"symbol", "chain", "change_24h", ...}]}  (45 items, 21 up / 24 down)
  * CoinGecko /global                        -> {"data": {"total_market_cap": {"usd"}, "market_cap_change_percentage_24h_usd", ...}}
  * CoinGecko /coins/markets (fallback)      -> [{"id", "symbol", "current_price", "price_change_percentage_24h", "market_cap"}]
  * alternative.me /fng                      -> {"data": [{"value": "29", "value_classification": "Fear"}]}
  * DefiLlama /v2/chains                     -> [{"name", "tvl", "tokenSymbol", ...}]  (no change field — trend needs historical)
  * DefiLlama /v2/historicalChainTvl/{chain} -> [{"date", "tvl"}]  (per-chain 7d change computed here)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.domains.telegram.rugmunchbot.config import COINGECKO
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.tools._client import make_client
from app.domains.telegram.rugmunchbot.tools.budget import budget, provider_for_url
from app.domains.telegram.rugmunchbot.tools.market import (
    _backend_fear_greed,
    _backend_prices,
    _coingecko_global,
    _coingecko_headers,
    _direct_fear_greed,
)
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.market_condition")

DEFILLAMA = "https://api.llama.fi/v2"

# ── Verdict thresholds (documented — the score is fully auditable) ─────────
_FEAR_GREED_RISK_ON = 55.0  # F&G >= 55 → risk-on lean (Greed / Extreme Greed band)
_FEAR_GREED_RISK_OFF = 40.0  # F&G <= 40 → risk-off lean (Fear / Extreme Fear band)
_MOMENTUM_RISK_ON = 2.0  # 24h change >= +2% → risk-on lean
_MOMENTUM_RISK_OFF = -2.0  # 24h change <= -2% → risk-off lean
_BREADTH_RISK_ON = 0.60  # pump_ratio >= 0.60 → risk-on lean
_BREADTH_RISK_OFF = 0.40  # pump_ratio <= 0.40 → risk-off lean
_TVL_TREND_RISK_ON = 5.0  # top-chain TVL 7d change >= +5% → risk-on lean
_TVL_TREND_RISK_OFF = -5.0  # 7d change <= -5% → risk-off lean
_VERDICT_RISK_ON_SCORE = 0.4  # score >= +0.4 → risk-on
_VERDICT_RISK_OFF_SCORE = -0.4  # score <= -0.4 → risk-off

# DefiLlama chain-name -> URL slug exceptions (top TVL chains probed 2026-08-14).
_TVL_SLUG_EXCEPTIONS: dict[str, str] = {
    "binance": "bsc",
    "binance smart chain": "bsc",
    "bnb": "bsc",
}


async def _http_get_any(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> Any:
    """GET + parse JSON that may be a dict OR a list.

    ``_client.http_get_json`` only returns dict bodies — DefiLlama and
    CoinGecko /coins/markets return arrays, so this budget-gated variant is
    needed. Returns None on any failure (never raises). Mirrors
    ``trend_algo._http_get_any`` but goes through the shared budget gate.
    """
    bucket = provider_for_url(url)
    if not budget.acquire(bucket):
        logger.debug("tool_api_budget skip provider=%s url=%.100s", bucket, url)
        return None
    try:
        async with make_client(timeout) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as exc:
        logger.debug("http_get_any failed url=%.100s: %s", url, exc)
        return None


# ── Signal fetchers (own backend first, external fallback) ─────────────────


async def _backend_trending_breadth(limit: int = 50) -> dict[str, Any] | None:
    """OUR trending breadth — how many tokens pumping vs dumping right now.

    Counts the real ``change_24h`` values in the backend trending feed. A
    token with an unknown change is not counted (real-data-only). Returns
    ``None`` when the feed is unreachable or has no countable items.
    """
    body = await backend_json_get("/api/v1/market/trending", params={"limit": min(max(int(limit), 1), 50)}, timeout=6.0)
    items = body.get("items") if body else None
    if not isinstance(items, list) or not items:
        return None
    pumping = dumping = flat = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        chg = it.get("change_24h")
        if not isinstance(chg, (int, float)):
            continue
        if chg > 0:
            pumping += 1
        elif chg < 0:
            dumping += 1
        else:
            flat += 1
    total = pumping + dumping + flat
    if total == 0:
        return None
    return {
        "pumping": pumping,
        "dumping": dumping,
        "flat": flat,
        "total": total,
        "pump_ratio": round(pumping / total, 3),
        "source": "backend:/api/v1/market/trending",
    }


def _chain_slug(name: str) -> str:
    """DefiLlama historicalChainTvl URL slug for a chain display name."""
    n = (name or "").strip().lower()
    return _TVL_SLUG_EXCEPTIONS.get(n, n)


def _tvl_change_7d(series: list[Any], lookback_days: int) -> float | None:
    """Percent TVL change over ``lookback_days`` for a DefiLlama series.

    ``series`` is the real [{"date", "tvl"}] payload (ascending). The
    reference point is the FIRST point at-or-after the cutoff; when no point
    is inside the window the oldest point is used. Returns None when the
    series cannot produce two positive TVL values (never invents numbers).
    """
    if len(series) < 2:
        return None
    last = series[-1]
    if not isinstance(last, dict):
        return None
    last_tvl = last.get("tvl")
    if not isinstance(last_tvl, (int, float)) or last_tvl <= 0:
        return None
    cutoff = time.time() - lookback_days * 86400
    ref_tvl: float | None = None
    for pt in series:
        if not isinstance(pt, dict):
            continue
        d = pt.get("date")
        tvl = pt.get("tvl")
        if isinstance(d, (int, float)) and d >= cutoff and isinstance(tvl, (int, float)) and tvl > 0:
            ref_tvl = float(tvl)
            break
    if ref_tvl is None:
        first = series[0]
        if not isinstance(first, dict):
            return None
        f0 = first.get("tvl")
        if not isinstance(f0, (int, float)) or f0 <= 0:
            return None
        ref_tvl = float(f0)
    return (float(last_tvl) - ref_tvl) / ref_tvl * 100.0


async def _defillama_top_chains(limit: int = 8) -> list[dict[str, Any]] | None:
    """Top chains by TVL from DefiLlama (list-capable fetch).

    Local variant of ``market._defillama_top_chains``: that helper goes
    through ``_client.http_get_json`` which only returns dict bodies, and
    DefiLlama /v2/chains is an ARRAY — so it always degrades to None. This
    version uses :func:`_http_get_any` (dict OR list) and actually returns
    the real top-chain TVL list. [REVIEW] — flag for dedupe with market.py.
    """
    chains = await _http_get_any(f"{DEFILLAMA}/chains", timeout=8.0)
    if not isinstance(chains, list):
        return None
    ranked = sorted(
        (c for c in chains if isinstance(c, dict)),
        key=lambda c: c.get("tvl") or 0,
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for c in ranked[:limit]:
        out.append({"name": c.get("name"), "tvl": c.get("tvl"), "tokenSymbol": c.get("tokenSymbol")})
    return out or None


async def _defillama_tvl_trend(
    top_chains: list[dict[str, Any]] | None,
    *,
    top_n: int = 3,
    lookback_days: int = 7,
) -> dict[str, Any] | None:
    """7-day TVL trend for the top ``top_n`` chains (DefiLlama historical data).

    ``top_chains`` comes from :func:`market._defillama_top_chains` (real,
    already ranked). Each top chain's historical series is fetched
    concurrently (bounded) and its 7d change computed from REAL points. A
    chain whose series is missing/empty is skipped — the trend is still
    reported for the chains that measured. Returns None when nothing measured.
    """
    if not top_chains:
        return None
    names = [
        str(c.get("name")) for c in top_chains[:top_n] if isinstance(c.get("name"), str) and str(c.get("name")).strip()
    ]
    if not names:
        return None
    sem = asyncio.Semaphore(3)

    async def one(slug: str) -> dict[str, Any] | None:
        """Fetch and score the 7-day TVL trend for one chain slug."""
        async with sem:
            series = await _http_get_any(f"{DEFILLAMA}/historicalChainTvl/{slug}", timeout=8.0)
        if not isinstance(series, list) or not series:
            return None
        change = _tvl_change_7d(series, lookback_days)
        if change is None:
            return None
        last = series[-1]
        return {
            "name": slug,
            "tvl": last.get("tvl") if isinstance(last, dict) else None,
            "change_7d": round(change, 2),
        }

    results = await asyncio.gather(*[one(_chain_slug(n)) for n in names])
    measured = [r for r in results if r is not None]
    if not measured:
        return None
    changes = sorted(float(r["change_7d"]) for r in measured)
    median = changes[len(changes) // 2]
    return {
        "median_change_7d": round(median, 2),
        "chains_measured": len(measured),
        "chains": measured,
        "source": "defillama:/v2/historicalChainTvl",
    }


async def _coingecko_markets_momentum() -> dict[str, dict[str, Any]] | None:
    """BTC/ETH/SOL momentum fallback: one CoinGecko /coins/markets call.

    Used only when the backend price feed is down. Returns real
    ``current_price`` + ``price_change_percentage_24h`` for the three majors.
    """
    body = await _http_get_any(
        f"{COINGECKO}/coins/markets",
        params={
            "vs_currency": "usd",
            "ids": "bitcoin,ethereum,solana",
            "price_change_percentage": "24h",
        },
        headers=_coingecko_headers(),
        timeout=6.0,
    )
    if not isinstance(body, list):
        return None
    out: dict[str, dict[str, Any]] = {}
    for c in body:
        if not isinstance(c, dict):
            continue
        coin_id = c.get("id")
        if coin_id not in ("bitcoin", "ethereum", "solana"):
            continue
        sym = (c.get("symbol") or "").upper()
        out[sym] = {
            "symbol": sym,
            "price_usd": c.get("current_price"),
            "change_24h": c.get("price_change_percentage_24h"),
            "source": "coingecko:/coins/markets",
        }
    return out or None


# ── Pure verdict engine (unit-testable, no I/O) ─────────────────────────────


def _vote(risk_on: bool, risk_off: bool) -> int:
    if risk_on:
        return 1
    if risk_off:
        return -1
    return 0


def _lean(vote: int) -> str:
    return {1: "supporting risk-on", -1: "supporting risk-off", 0: "neutral"}[vote]


def _fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


def _as_float(value: Any) -> float | None:
    """Coerce a REAL source value to float (numeric str or number).

    alternative.me returns the Fear & Greed value as a string ("29"); the
    backend returns an int (29). Both are the same real number — normalize
    so the verdict engine always sees a numeric value. Non-numeric values
    return None (the signal is treated as absent, never guessed).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None
    return None


def assess_market_condition(signals: dict[str, Any]) -> dict[str, Any]:
    """Deterministic risk-on/risk-off/neutral verdict from REAL signal values.

    Pure function — no I/O, no random, no LLM. Each PRESENT signal contributes
    one vote (+1 risk-on / -1 risk-off / 0 neutral):

      * ``fear_greed.value``                  >= 55 -> +1 ; <= 40 -> -1
      * ``btc.change_24h`` / ``eth.change_24h`` >= +2% -> +1 ; <= -2% -> -1
      * ``global_market_cap_change_24h``      >= +2% -> +1 ; <= -2% -> -1
      * ``breadth.pump_ratio``                >= 0.60 -> +1 ; <= 0.40 -> -1
      * ``tvl_trend.median_change_7d``        >= +5% -> +1 ; <= -5% -> -1

    ``score = votes / count`` in [-1, 1]; verdict: >= +0.4 risk-on, <= -0.4
    risk-off, else neutral. ``confidence``: >= 4 signals high, >= 2 medium,
    else low. ``reasons``: one line per present signal carrying ONLY the real
    numbers it saw.
    """
    votes: list[int] = []
    reasons: list[str] = []

    fg = signals.get("fear_greed")
    if isinstance(fg, dict) and isinstance(fg.get("value"), (int, float)):
        v = float(fg["value"])
        vote = _vote(v >= _FEAR_GREED_RISK_ON, v <= _FEAR_GREED_RISK_OFF)
        votes.append(vote)
        reasons.append(f"Fear & Greed {v:.0f} ({fg.get('classification') or 'n/a'}) — {_lean(vote)}")

    for key, label in (("btc", "BTC"), ("eth", "ETH")):
        coin = signals.get(key)
        if not isinstance(coin, dict) or not isinstance(coin.get("change_24h"), (int, float)):
            continue
        chg = float(coin["change_24h"])
        vote = _vote(chg >= _MOMENTUM_RISK_ON, chg <= _MOMENTUM_RISK_OFF)
        votes.append(vote)
        reasons.append(f"{label} 24h {_fmt_pct(chg)} — {_lean(vote)}")

    mcap_chg = signals.get("global_market_cap_change_24h")
    if isinstance(mcap_chg, (int, float)):
        vote = _vote(mcap_chg >= _MOMENTUM_RISK_ON, mcap_chg <= _MOMENTUM_RISK_OFF)
        votes.append(vote)
        reasons.append(f"Global market cap 24h {_fmt_pct(float(mcap_chg))} — {_lean(vote)}")

    breadth = signals.get("breadth")
    if isinstance(breadth, dict) and isinstance(breadth.get("pump_ratio"), (int, float)):
        ratio = float(breadth["pump_ratio"])
        vote = _vote(ratio >= _BREADTH_RISK_ON, ratio <= _BREADTH_RISK_OFF)
        votes.append(vote)
        reasons.append(
            f"Our breadth {breadth.get('pumping')} up / {breadth.get('dumping')} down "
            f"(pump ratio {ratio:.2f}) — {_lean(vote)}"
        )

    tvl = signals.get("tvl_trend")
    if isinstance(tvl, dict) and isinstance(tvl.get("median_change_7d"), (int, float)):
        change = float(tvl["median_change_7d"])
        vote = _vote(change >= _TVL_TREND_RISK_ON, change <= _TVL_TREND_RISK_OFF)
        votes.append(vote)
        reasons.append(f"Top-chain TVL 7d {_fmt_pct(change)} ({tvl.get('chains_measured')} chains) — {_lean(vote)}")

    count = len(votes)
    if count == 0:
        return {"verdict": "neutral", "confidence": "low", "score": 0.0, "reasons": []}

    score = round(sum(votes) / count, 3)
    if score >= _VERDICT_RISK_ON_SCORE:
        verdict = "risk_on"
    elif score <= _VERDICT_RISK_OFF_SCORE:
        verdict = "risk_off"
    else:
        verdict = "neutral"
    confidence = "high" if count >= 4 else ("medium" if count >= 2 else "low")
    return {"verdict": verdict, "confidence": confidence, "score": score, "reasons": reasons}


# ── The tool ────────────────────────────────────────────────────────────────


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


async def market_condition(*, chain: str = "global", **_ctx: Any) -> ToolResult:
    """Synthesized macro read: verdict + real signals for "how's the market".

    Backend-first sources, CoinGecko / DefiLlama / alternative.me fallbacks.
    ``**_ctx`` tolerates the shared registry context (e.g. ``history``) so the
    tool keeps working when chat layers forward extra kwargs. Returns
    ``ok=False`` only when every signal source is unreachable — never a guess.
    """
    prices, fear, breadth, global_data, chains = await asyncio.gather(
        _backend_prices(),
        _backend_fear_greed(),
        _backend_trending_breadth(50),
        _coingecko_global(),
        _defillama_top_chains(8),
    )
    # Fear & Greed direct fallback when the backend feed is down.
    if fear is None:
        fear = await _direct_fear_greed()
    # BTC/ETH momentum fallback when the backend price feed is down.
    fallback_prices: dict[str, dict[str, Any]] | None = None
    if prices is None:
        fallback_prices = await _coingecko_markets_momentum()

    tvl_trend = await _defillama_tvl_trend(chains, top_n=3, lookback_days=7)

    signals: dict[str, Any] = {}
    sources: list[str] = []
    symbols: list[str] = []

    if prices:
        signals["btc"] = {
            "symbol": "BTC",
            "price_usd": (prices.get("bitcoin") or {}).get("usd"),
            "change_24h": (prices.get("bitcoin") or {}).get("usd_24h_change"),
            "source": "backend:/api/v1/databus/market/prices",
        }
        signals["eth"] = {
            "symbol": "ETH",
            "price_usd": (prices.get("ethereum") or {}).get("usd"),
            "change_24h": (prices.get("ethereum") or {}).get("usd_24h_change"),
            "source": "backend:/api/v1/databus/market/prices",
        }
        symbols = ["BTC", "ETH"]
        sources.append("backend:/api/v1/databus/market/prices")
    elif fallback_prices:
        if "BTC" in fallback_prices:
            signals["btc"] = fallback_prices["BTC"]
            symbols.append("BTC")
        if "ETH" in fallback_prices:
            signals["eth"] = fallback_prices["ETH"]
            symbols.append("ETH")
        sources.append("coingecko:/coins/markets")

    if fear:
        fg_value = _as_float(fear.get("value"))
        if fg_value is not None:
            signals["fear_greed"] = {
                "value": fg_value,
                "classification": fear.get("classification"),
                "source": fear.get("source") or "fear_greed",
            }
            sources.append(f"{fear.get('source') or 'fear_greed'}")

    if global_data:
        signals["global_market_cap_usd"] = (global_data.get("total_market_cap") or {}).get("usd")
        signals["global_market_cap_change_24h"] = global_data.get("market_cap_change_percentage_24h_usd")
        sources.append("coingecko:/global")

    if breadth:
        signals["breadth"] = breadth
        sources.append(breadth.get("source") or "backend:/api/v1/market/trending")

    if chains:
        signals["top_chains_tvl"] = chains
        sources.append("defillama:/v2/chains")

    if tvl_trend:
        signals["tvl_trend"] = tvl_trend
        sources.append(tvl_trend.get("source") or "defillama:/v2/historicalChainTvl")

    if not signals:
        return ToolResult(
            tool="market_condition",
            ok=False,
            error="no live market signals (backend + CoinGecko + DefiLlama + alternative.me unavailable)",
        )

    verdict = assess_market_condition(signals)
    payload: dict[str, Any] = {
        "chain": chain or "global",
        "verdict": verdict["verdict"],
        "confidence": verdict["confidence"],
        "score": verdict["score"],
        "reasons": verdict["reasons"],
        "signals": signals,
        "sources": sorted(set(sources)),
        "updated_at": _iso_now(),
    }
    return ToolResult(
        tool="market_condition",
        ok=True,
        data=payload,
        symbols=symbols,
        addresses=[],
        source="; ".join(sorted(set(sources))),
    )
