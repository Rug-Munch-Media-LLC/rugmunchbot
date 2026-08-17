"""technical_analysis tool — real RSI-14 / EMA-9/21 / MACD / support-resistance.

Data cascade (own data first, matching the /ta command and the backend
RugCharts engine):

  1. Our backend ``GET /api/v1/rugcharts/ohlcv/{chain}/{pair}`` — the RugCharts
     engine itself tries the chain-indexer ClickHouse materialized view first
     (``wallet_memory.dex_ohlcv_hourly``) and falls back to GeckoTerminal
     server-side (see ``app/routers/rugcharts_helpers``).
  2. GeckoTerminal direct (``/api/v2/networks/{network}/pools/{pair}/ohlcv``) —
     used when the backend is unreachable or returns no candles.
  3. DexScreener pair snapshot — used for pair resolution + price context.
     DexScreener exposes NO candles, so when neither candle source is
     reachable the tool returns the REAL snapshot with
     ``indicators_available=false`` instead of fabricating RSI/EMA/MACD.

The payload always carries a ``market`` block (price, 1h/6h/24h change,
volume-24h, liquidity, market cap, FDV — real DexScreener values, plus
candle-window change/high/low and distance from the window high/low when
candles exist) so the chat LLM can answer TA/market asks with concrete
numbers, not a one-liner.

The indicator math is REUSED from
``app.domains.telegram.rugmunchbot.ta_indicators`` (``analyze_candles``:
RSI-14, EMA-9/21, MACD 12/26/9, support/resistance, trend, volume) — pure
Python, no TA-Lib dependency. The verdict is a transparent rule-of-thumb
score over those real indicator values. Every number in the payload comes
from a live source — never invented.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.domains.telegram.rugmunchbot.config import DEXSCREENER
from app.domains.telegram.rugmunchbot.data_resolver import resolve_with_fallback
from app.domains.telegram.rugmunchbot.support.logging import get_logger
from app.domains.telegram.rugmunchbot.ta_indicators import analyze_candles
from app.domains.telegram.rugmunchbot.tools._client import http_get_json
from app.domains.telegram.rugmunchbot.tools.registry import ToolResult, backend_json_get

logger = get_logger("rmi_bot.tools.ta")

# GeckoTerminal network ids — mirrors ``app/routers/rugcharts_helpers.GECKO_NETWORKS``
# (the backend's canonical mapping). Defined locally because importing the
# backend router helper is not possible inside the bot process (settings guard
# in ``app.core``) and the bot tools stay self-contained.
_GECKO_NETWORKS: dict[str, str] = {
    "solana": "solana",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "tron": "tron",
    "polygon": "polygon_pos",
    "avalanche": "avax",
    "optimism": "optimism",
}

# Chain aliases -> canonical chain id — subset of
# ``tools/trend_algo._CHAIN_ALIASES`` (kept local to avoid a private
# cross-tool import; add aliases here when new chains are supported).
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
    "base": "base",
    "polygon": "polygon",
    "matic": "polygon",
    "polygon-pos": "polygon",
    "optimism": "optimism",
    "op": "optimism",
    "avax": "avalanche",
    "avalanche": "avalanche",
    "tron": "tron",
    "trx": "tron",
    "linea": "linea",
    "blast": "blast",
    "sonic": "sonic",
    "zksync": "zksync",
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

_GECKO_OHLCV_URL = "https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pair}/ohlcv/hour"

# (candles, source_label) — the shape both candle primaries return so
# ``resolve_with_fallback`` enforces one contract regardless of source.
_Candle = dict[str, Any]
_CandleResult = tuple[list[_Candle], str]


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_chain(chain: str) -> str:
    key = (chain or "").strip().lower()
    return _CHAIN_ALIASES.get(key, key)


# ── Pair resolution (DexScreener; real pairs only) ─────────────────────────


def _normalize_pairs(data: Any) -> list[dict[str, Any]]:
    """Normalize DexScreener pair payloads (dict {"pairs": [...]} or list)."""
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
    elif isinstance(data, list):
        pairs = data
    else:
        pairs = []
    return [p for p in pairs if isinstance(p, dict)]


def _is_pair(p: dict[str, Any]) -> bool:
    return bool(p.get("pairAddress")) and isinstance(p.get("baseToken"), dict)


def _pick_best_pair(pairs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Highest-24h-volume pair (real volume from the source)."""
    best: dict[str, Any] | None = None
    best_vol = -1.0
    for p in pairs:
        vol = _to_float((p.get("volume") or {}).get("h24")) or 0.0
        if vol > best_vol:
            best_vol = vol
            best = p
    return best


async def _resolve_pair(address: str, symbol: str, chain: str) -> dict[str, Any] | None:
    """Resolve a DexScreener pair for an address or ``$SYMBOL``.

    Address queries use the exact token endpoint; symbol queries use search
    and prefer a pair whose baseToken symbol matches exactly. ``chain`` (when
    given) filters pairs; the winner is the highest-24h-volume pair.
    """
    if address:
        body = await http_get_json(f"{DEXSCREENER}/tokens/{address}", timeout=6.0)
        pairs = [p for p in _normalize_pairs(body) if _is_pair(p)]
        if chain:
            want = _normalize_chain(chain)
            matched = [p for p in pairs if _normalize_chain(str(p.get("chainId") or "")) == want]
            if matched:
                pairs = matched
        return _pick_best_pair(pairs)

    q = (symbol or "").strip().upper()
    if not q:
        return None
    body = await http_get_json(f"{DEXSCREENER}/search", params={"q": q}, timeout=6.0)
    pairs = [p for p in _normalize_pairs(body) if _is_pair(p)]
    if not pairs:
        return None
    exact = [p for p in pairs if ((p.get("baseToken") or {}).get("symbol") or "").upper() == q]
    if chain:
        want = _normalize_chain(chain)
        exact = [p for p in exact if _normalize_chain(str(p.get("chainId") or "")) == want] or exact
    return _pick_best_pair(exact or pairs)


def _pair_snapshot(pair: dict[str, Any], address: str, symbol: str) -> dict[str, Any]:
    """Real DexScreener pair metrics (never synthesized)."""
    base = pair.get("baseToken") or {}
    volume = pair.get("volume") or {}
    price_change = pair.get("priceChange") or {}
    liquidity_raw = pair.get("liquidity")
    liquidity = liquidity_raw if isinstance(liquidity_raw, dict) else {}
    return {
        "address": address or base.get("address") or "",
        "symbol": (base.get("symbol") or "").strip().upper() or (symbol or "").strip().upper(),
        "name": (base.get("name") or "").strip(),
        "chain": _normalize_chain(str(pair.get("chainId") or "")),
        "dex": pair.get("dexId") or "",
        "pair_address": pair.get("pairAddress") or "",
        "price_usd": _to_float(pair.get("priceUsd")),
        "change_1h": _to_float(price_change.get("h1")),
        "change_6h": _to_float(price_change.get("h6")),
        "change_24h": _to_float(price_change.get("h24")),
        "volume_24h": _to_float(volume.get("h24")),
        "liquidity_usd": _to_float(liquidity.get("usd")),
        "market_cap": _to_float(pair.get("marketCap")),
        "fdv": _to_float(pair.get("fdv")),
    }


def _market_context(snapshot: dict[str, Any], analysis: dict[str, Any] | None) -> dict[str, Any]:
    """Flat live-market read merged from the real DexScreener pair snapshot
    and (when candles were available) the candle analysis.

    Only real source values are included — ``None`` fields are omitted so the
    LLM never sees fabricated numbers. ``distance_from_window_*`` is measured
    against the candle WINDOW high/low, deliberately NOT an all-time high: no
    live ATH feed exists for arbitrary DEX pairs, and the key stays honest so
    the model phrases it as "recent range" rather than "ATH".
    """
    ctx: dict[str, Any] = {}
    for key, value in (
        ("price_usd", snapshot.get("price_usd")),
        ("change_1h", snapshot.get("change_1h")),
        ("change_6h", snapshot.get("change_6h")),
        ("change_24h", snapshot.get("change_24h")),
        ("volume_24h", snapshot.get("volume_24h")),
        ("liquidity_usd", snapshot.get("liquidity_usd")),
        ("market_cap", snapshot.get("market_cap")),
        ("fdv", snapshot.get("fdv")),
    ):
        if value is not None:
            ctx[key] = value
    if analysis is not None:
        if analysis.get("change_pct") is not None:
            ctx["change_window_pct"] = analysis.get("change_pct")
        if analysis.get("high") is not None:
            ctx["window_high"] = analysis.get("high")
        if analysis.get("low") is not None:
            ctx["window_low"] = analysis.get("low")
        price = analysis.get("price")
        high = analysis.get("high")
        low = analysis.get("low")
        if price is not None and high and high > 0:
            ctx["distance_from_window_high_pct"] = (high - price) / high * 100
        if price is not None and low and low > 0:
            ctx["distance_from_window_low_pct"] = (price - low) / low * 100
    return ctx


# ── OHLCV candles: own data first, GeckoTerminal direct fallback ───────────


async def _backend_candles(chain: str, pair_address: str) -> _CandleResult:
    """Own-data candles via the backend RugCharts engine (ClickHouse first,
    GeckoTerminal server-side fallback). Returns (candles, source_label)."""
    body = await backend_json_get(
        f"/api/v1/rugcharts/ohlcv/{chain}/{pair_address}",
        params={"timeframe": "1h", "limit": 100},
        timeout=6.0,
    )
    if not isinstance(body, dict):
        return [], ""
    data = body.get("data")
    candles = data.get("candles") if isinstance(data, dict) else None
    if isinstance(candles, list) and candles:
        return candles, f"backend:/api/v1/rugcharts/ohlcv/{chain}/{pair_address}"
    return [], ""


async def _gecko_candles(chain: str, pair_address: str) -> _CandleResult:
    """GeckoTerminal direct OHLCV (same query pattern as the backend helper
    ``app/routers/rugcharts_helpers._fetch_ohlcv_gecko``)."""
    network = _GECKO_NETWORKS.get(_normalize_chain(chain), _normalize_chain(chain))
    body = await http_get_json(
        _GECKO_OHLCV_URL.format(network=network, pair=pair_address),
        params={"aggregate": 1, "limit": 100, "currency": "usd"},
        timeout=6.0,
    )
    if not isinstance(body, dict):
        return [], ""
    attrs = body.get("data", {}).get("attributes") if isinstance(body.get("data"), dict) else None
    ohlcv_list = attrs.get("ohlcv_list") if isinstance(attrs, dict) else None
    if not isinstance(ohlcv_list, list):
        return [], ""
    candles: list[_Candle] = []
    for c in reversed(ohlcv_list):  # source is newest-first; we want chronological
        if not isinstance(c, list) or len(c) < 6:
            continue
        try:
            candles.append(
                {
                    "time": int(c[0]),
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
            )
        except (TypeError, ValueError):
            continue
    if candles:
        return candles, f"geckoterminal:direct:{network}:{pair_address}"
    return [], ""


async def _fetch_candles(chain: str, pair_address: str) -> _CandleResult:
    """Run the own-data-first candle cascade (backend -> GeckoTerminal)."""
    try:
        return await resolve_with_fallback(
            f"ta_ohlcv:{chain}:{pair_address[:10]}",
            lambda: _backend_candles(chain, pair_address),
            lambda: _gecko_candles(chain, pair_address),
            timeout_secs=5.0,
            is_valid=lambda res: bool(res and isinstance(res[0], list) and len(res[0]) >= 2),
        )
    except Exception as exc:  # pragma: no cover — both sources failed
        logger.debug("ta_ohlcv both sources failed: %s", exc)
        return [], ""


# ── Verdict + chat-friendly summary (pure functions over real indicators) ───


def _verdict(analysis: dict[str, Any]) -> dict[str, Any]:
    """Transparent rule-of-thumb bias from the real indicator digest.

    Scores EMA structure (+/-2), EMA cross (+/-1), MACD (+/-1) and RSI
    tilt (+/-1); ``bullish`` when score >= 2, ``bearish`` when <= -2,
    ``neutral`` otherwise. Overbought/oversold RSI is reported as risk in
    the reasons instead of contributing to the tilt.
    """
    score = 0.0
    reasons: list[str] = []

    trend = analysis.get("trend") or "NEUTRAL"
    if trend == "BULLISH":
        score += 2
        reasons.append("EMA-9 above EMA-21")
    elif trend == "BEARISH":
        score -= 2
        reasons.append("EMA-9 below EMA-21")
    else:
        reasons.append("EMAs flat")

    cross = analysis.get("ema_cross")
    if cross == "BULLISH CROSS":
        score += 1
        reasons.append("EMA bullish cross")
    elif cross == "BEARISH CROSS":
        score -= 1
        reasons.append("EMA bearish cross")

    macd_label = analysis.get("macd_label")
    if macd_label == "Bullish":
        score += 1
        reasons.append("MACD above signal")
    elif macd_label == "Bearish":
        score -= 1
        reasons.append("MACD below signal")

    rsi_value = analysis.get("rsi")
    if rsi_value is not None:
        if rsi_value >= 70:
            reasons.append(f"RSI {rsi_value:.1f} overbought (extension risk)")
        elif rsi_value <= 30:
            reasons.append(f"RSI {rsi_value:.1f} oversold (bounce risk)")
        elif rsi_value > 55:
            score += 1
            reasons.append(f"RSI {rsi_value:.1f} firm")
        elif rsi_value < 45:
            score -= 1
            reasons.append(f"RSI {rsi_value:.1f} soft")
        else:
            reasons.append(f"RSI {rsi_value:.1f} neutral")

    bias = "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"
    lead = reasons[0] if reasons else "no signal"
    one_liner = f"{bias.upper()} (score {score:+.0f}) — {lead}"
    return {"bias": bias, "score": round(score, 1), "reasons": reasons, "one_liner": one_liner}


def _prediction_read(analysis: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    """Honest short-horizon leaning read from the real indicator digest.

    Combines the rule-of-thumb ``verdict`` bias with momentum direction and
    the pump-risk screen. The read is explicitly labelled heuristic and
    never a guarantee: the LLM is expected to present it as "leaning" with
    reasons, not as a forecast. Confidence is data-availability driven
    (candle count + how many signals agree).
    """
    bias = verdict.get("bias", "neutral")
    momentum = analysis.get("momentum") or "N/A"
    pump = analysis.get("pump_risk") or {}

    drivers: list[str] = list(verdict.get("reasons") or [])
    if momentum not in ("N/A",):
        drivers.append(f"momentum {momentum}")
    if pump.get("level") and pump["level"] != "LOW":
        drivers.append(f"pump-risk {pump['level']}")

    # Direction disagreement knocks the lean toward neutral; extreme
    # pump-risk stays flagged even when the bias is bullish.
    lean = bias
    if (bias == "bullish" and momentum in ("FALLING", "FADING")) or (
        bias == "bearish" and momentum in ("RISING", "ACCELERATING")
    ):
        lean = "neutral"
    if pump.get("level") == "HIGH" and lean == "bullish":
        lean = "neutral"

    n = analysis.get("candles") or 0
    if n < 30:
        confidence = "low"
    elif n < 60 or lean == "neutral" or pump.get("level") == "HIGH":
        confidence = "medium"
    else:
        confidence = "high"

    return {
        "leaning": lean,
        "confidence": confidence,
        "horizon": "next 12-24h (1h candles)",
        "key_drivers": drivers[:6],
        "pump_risk": {"level": pump.get("level", "LOW"), "score": pump.get("score", 0)},
        "note": "heuristic read from live indicators — not financial advice, not a guarantee",
    }


def _summary_text(analysis: dict[str, Any]) -> str:
    """Compact one-line metric digest (chat-friendly; the LLM renders it)."""
    rsi_value = analysis.get("rsi")
    rsi_part = (
        f"RSI-14 {rsi_value:.1f} ({analysis.get('rsi_label') or 'n/a'})" if rsi_value is not None else "RSI-14 n/a"
    )

    ema9 = analysis.get("ema9")
    ema21 = analysis.get("ema21")
    ema_part = f"EMA9 {ema9:.6g} / EMA21 {ema21:.6g}" if ema9 is not None and ema21 is not None else "EMA n/a"

    macd_part = f"MACD {analysis.get('macd_label') or 'n/a'}"

    support = analysis.get("support")
    resistance = analysis.get("resistance")
    sr_part = f"S/R {support:.6g}/{resistance:.6g}" if support is not None and resistance is not None else "S/R n/a"

    vol_part = f"vol {analysis.get('volatility_pct'):.1f}%" if analysis.get("volatility_pct") is not None else "vol n/a"
    mom_part = f"mom {analysis.get('momentum') or 'n/a'}"
    pump = analysis.get("pump_risk") or {}
    pump_part = f"pump-risk {pump.get('level', 'n/a')}"

    return (
        f"{rsi_part} · {ema_part} · {macd_part} · {sr_part} · "
        f"trend {analysis.get('trend') or 'NEUTRAL'} · {vol_part} · {mom_part} · {pump_part}"
    )


# ── Tool handler ───────────────────────────────────────────────────────────


async def technical_analysis(*, address: str = "", symbol: str = "", chain: str = "") -> ToolResult:
    """Real technical analysis for a token address or ``$SYMBOL``.

    Resolves the pair (DexScreener), fetches 1h OHLCV own-data first
    (backend RugCharts/ClickHouse -> GeckoTerminal), then computes RSI-14,
    EMA-9/21, MACD, support/resistance and a trend verdict with the reused
    pure-Python ``ta_indicators`` math.

    Args:
        address: Token contract address (EVM or Solana).
        symbol: ``$SYMBOL`` (used when no address is given).
        chain: Optional chain filter ("ethereum", "solana", "base", ...).

    Returns:
        ToolResult with structured indicators + verdict + a compact
        ``summary_text`` line. ``ok=False`` only when no pair can be
        resolved. When candles are unavailable the tool still returns
        ``ok=True`` with the REAL DexScreener snapshot and
        ``indicators_available=false`` — indicators are never fabricated.
    """
    pair = await _resolve_pair(address, symbol, chain)
    if pair is None:
        return ToolResult(
            tool="technical_analysis",
            ok=False,
            error="could not resolve a trading pair for that symbol/address (DexScreener unreachable or no match)",
        )

    resolved_chain = _normalize_chain(chain or str(pair.get("chainId") or ""))
    pair_address = str(pair.get("pairAddress") or "")
    snapshot = _pair_snapshot(pair, address, symbol)

    candles, source = await _fetch_candles(resolved_chain, pair_address)

    if candles:
        analysis = analyze_candles(candles)
        if analysis is None:
            return ToolResult(
                tool="technical_analysis",
                ok=False,
                error="OHLCV candles returned but could not be parsed (malformed payload)",
            )
        verdict = _verdict(analysis)
        indicators = {
            "price": analysis.get("price"),
            "change_pct": analysis.get("change_pct"),
            "candles": analysis.get("candles"),
            "rsi_14": analysis.get("rsi"),
            "rsi_label": analysis.get("rsi_label"),
            "ema_9": analysis.get("ema9"),
            "ema_21": analysis.get("ema21"),
            "ema_cross": analysis.get("ema_cross"),
            "macd_line": analysis.get("macd_line"),
            "macd_signal": analysis.get("macd_signal"),
            "macd_hist": analysis.get("macd_hist"),
            "macd_label": analysis.get("macd_label"),
            "support": analysis.get("support"),
            "resistance": analysis.get("resistance"),
            "sma_20": analysis.get("sma_20"),
            "bollinger_upper": analysis.get("bollinger_upper"),
            "bollinger_middle": analysis.get("bollinger_middle"),
            "bollinger_lower": analysis.get("bollinger_lower"),
            "bollinger_percent_b": analysis.get("bollinger_percent_b"),
            "bollinger_bandwidth": analysis.get("bollinger_bandwidth"),
            "atr_14": analysis.get("atr_14"),
            "atr_pct": analysis.get("atr_pct"),
            "volatility_pct": analysis.get("volatility_pct"),
            "roc_3h": analysis.get("roc_3h"),
            "roc_12h": analysis.get("roc_12h"),
            "roc_24h": analysis.get("roc_24h"),
            "momentum": analysis.get("momentum"),
            "pump_risk": analysis.get("pump_risk"),
            "trend": analysis.get("trend"),
            "volume_ratio": analysis.get("volume_ratio"),
            "volume_trend": analysis.get("volume_trend"),
        }
        return ToolResult(
            tool="technical_analysis",
            ok=True,
            data={
                "query": {"address": address, "symbol": symbol, "chain": resolved_chain},
                "symbol": snapshot["symbol"],
                "name": snapshot["name"],
                "chain": resolved_chain,
                "pair_address": pair_address,
                "timeframe": "1h",
                "indicators_available": True,
                "indicators": indicators,
                "market": _market_context(snapshot, analysis),
                "verdict": verdict,
                "prediction": _prediction_read(analysis, verdict),
                "summary_text": _summary_text(analysis),
                "updated_at": datetime.now(UTC).isoformat(),
            },
            symbols=[snapshot["symbol"]] if snapshot["symbol"] else [],
            addresses=[snapshot["address"]] if snapshot["address"] else [],
            source=source or "ohlcv:unknown",
        )

    # Honest degraded path: no candle source reachable. DexScreener exposes
    # no OHLCV, so only the real snapshot metrics are returned.
    momentum: list[str] = []
    if snapshot["change_1h"] is not None:
        momentum.append(f"1h {snapshot['change_1h']:+.2f}%")
    if snapshot["change_6h"] is not None:
        momentum.append(f"6h {snapshot['change_6h']:+.2f}%")
    if snapshot["change_24h"] is not None:
        momentum.append(f"24h {snapshot['change_24h']:+.2f}%")
    momentum_text = " · ".join(momentum) if momentum else "no change data"
    one_liner = f"INSUFFICIENT DATA (score 0) — no OHLCV candles right now; live snapshot only: {momentum_text}"
    return ToolResult(
        tool="technical_analysis",
        ok=True,
        data={
            "query": {"address": address, "symbol": symbol, "chain": resolved_chain},
            "symbol": snapshot["symbol"],
            "name": snapshot["name"],
            "chain": resolved_chain,
            "pair_address": pair_address,
            "timeframe": "1h",
            "indicators_available": False,
            "indicators": None,
            "snapshot": snapshot,
            "market": _market_context(snapshot, None),
            "verdict": {
                "bias": "insufficient_data",
                "score": 0.0,
                "reasons": [
                    "backend RugCharts (ClickHouse + GeckoTerminal) returned no candles; "
                    "DexScreener exposes no OHLCV history"
                ],
                "one_liner": one_liner,
            },
            "prediction": {
                "leaning": "insufficient_data",
                "confidence": "low",
                "horizon": "next 12-24h (1h candles)",
                "key_drivers": ["no OHLCV candle history to base a read on"],
                "note": "heuristic read from live indicators — not financial advice, not a guarantee",
            },
            "summary_text": f"No OHLCV candle history available right now — live snapshot: {momentum_text}",
            "updated_at": datetime.now(UTC).isoformat(),
        },
        symbols=[snapshot["symbol"]] if snapshot["symbol"] else [],
        addresses=[snapshot["address"]] if snapshot["address"] else [],
        source="dexscreener:snapshot (no candles)",
    )


# ── Registration (self-owned; invoked by tools/__init__._register_defaults) ─


def register_tool(reg: Any) -> None:
    """Register this tool on the shared registry (see trend_algo for notes)."""
    reg.register("technical_analysis", technical_analysis, timeout=12.0, ttl=120.0)
