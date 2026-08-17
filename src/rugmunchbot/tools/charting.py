"""chart tool — compact ASCII price-action chart + TA digest for RugMunchBot.

Answers *"chart for $BONK"* / *"price chart"* with a text-only, Telegram-
monospace friendly chart (wrapped in backticks by the chat layer). No image
generation.

Data cascade (own data first — same order as ``tools/ta.py``):

  1. OUR backend ``GET /api/v1/rugcharts/ohlcv/{chain}/{pair}`` — the RugCharts
     engine (chain-indexer ClickHouse materialized view first, GeckoTerminal
     server-side fallback). ``timeframe=1h`` for 24h charts, ``1d`` for 7d.
  2. GeckoTerminal direct (``/ohlcv/hour`` or ``/ohlcv/day``) — used when the
     backend is unreachable or returns no candles.
  3. DexScreener pair snapshot — REAL snapshot metrics when no candle source
     is reachable (honest degraded chart; never fabricated).

The chart body: a one-line ASCII sparkline (close series), a per-candle
direction row (candlestick form), a compact vertical price ladder, and the TA
digest (RSI-14 / EMA-9/21 / MACD / support-resistance / trend — reused pure
math from ``ta_indicators.analyze_candles``).

Self-contained on purpose: chain maps / pair resolution / candle cascade are
local copies because the codebase convention keeps these per-tool to avoid
private cross-tool imports (keep in sync with ``tools/ta.py``).
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

logger = get_logger("rmi_bot.tools.charting")

# GeckoTerminal network ids — mirrors ``app/routers/rugcharts_helpers.GECKO_NETWORKS``
# and ``tools/ta.py`` (keep in sync).
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

# Chain aliases -> canonical chain id — subset of ``tools/trend_algo._CHAIN_ALIASES``
# and ``tools/ta.py._CHAIN_ALIASES`` (kept local; keep in sync).
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

_GECKO_OHLCV_URL = "https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pair}/ohlcv/{unit}"

# user timeframe -> (backend candle unit, gecko endpoint suffix, candles shown)
_TIMEFRAMES: dict[str, tuple[str, str, int]] = {
    "24h": ("1h", "hour", 24),
    "7d": ("1d", "day", 7),
}

# (candles, source_label) — the shape both candle primaries return so
# ``resolve_with_fallback`` enforces one contract regardless of source.
_Candle = dict[str, Any]
_CandleResult = tuple[list[_Candle], str]

# ASCII sparkline ladder (low -> high).
_SPARK_CHARS = "_.,-=*#"


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
    }


# ── OHLCV candles: own data first, GeckoTerminal direct fallback ───────────


async def _backend_candles(chain: str, pair_address: str, unit: str) -> _CandleResult:
    """Own-data candles via the backend RugCharts engine (ClickHouse first,
    GeckoTerminal server-side fallback). Returns (candles, source_label)."""
    body = await backend_json_get(
        f"/api/v1/rugcharts/ohlcv/{chain}/{pair_address}",
        params={"timeframe": unit, "limit": 100},
        timeout=6.0,
    )
    if not isinstance(body, dict):
        return [], ""
    data = body.get("data")
    candles = data.get("candles") if isinstance(data, dict) else None
    if isinstance(candles, list) and candles:
        return candles, f"backend:/api/v1/rugcharts/ohlcv/{chain}/{pair_address}"
    return [], ""


async def _gecko_candles(chain: str, pair_address: str, unit: str) -> _CandleResult:
    """GeckoTerminal direct OHLCV (same query pattern as the backend helper
    ``app/routers/rugcharts_helpers._fetch_ohlcv_gecko``)."""
    network = _GECKO_NETWORKS.get(_normalize_chain(chain), _normalize_chain(chain))
    body = await http_get_json(
        _GECKO_OHLCV_URL.format(network=network, pair=pair_address, unit=unit),
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


async def _fetch_candles(chain: str, pair_address: str, unit: str, gecko_unit: str) -> _CandleResult:
    """Run the own-data-first candle cascade (backend -> GeckoTerminal).

    ``unit`` is the backend candle timeframe ("1h"/"1d"); ``gecko_unit`` the
    GeckoTerminal endpoint suffix ("hour"/"day").
    """
    try:
        return await resolve_with_fallback(
            f"chart_ohlcv:{unit}:{chain}:{pair_address[:10]}",
            lambda: _backend_candles(chain, pair_address, unit),
            lambda: _gecko_candles(chain, pair_address, gecko_unit),
            timeout_secs=5.0,
            is_valid=lambda res: bool(res and isinstance(res[0], list) and len(res[0]) >= 2),
        )
    except Exception as exc:  # pragma: no cover — both sources failed
        logger.debug("chart_ohlcv both sources failed: %s", exc)
        return [], ""


def _slice_candles(candles: list[_Candle], timeframe: str) -> list[_Candle]:
    """Last N candles for the requested timeframe (24 hourly / 7 daily)."""
    n = _TIMEFRAMES.get(timeframe, ("1h", "hour", 24))[2]
    return candles[-n:] if candles else []


# ── ASCII rendering (pure functions; unit-testable) ────────────────────────


def _sparkline(closes: list[float], width: int = 48) -> str:
    """One-line ASCII sparkline of the close series (normalized to the
    observed min/max). ``width`` caps the character count (even sampling)."""
    if not closes:
        return ""
    lo = min(closes)
    hi = max(closes)
    if len(closes) <= width:
        sampled = closes
    else:
        idxs = [round(i * (len(closes) - 1) / (width - 1)) for i in range(width)]
        sampled = [closes[i] for i in idxs]
    span = hi - lo
    out: list[str] = []
    for v in sampled:
        level = len(_SPARK_CHARS) // 2 if span < 1e-12 else round((v - lo) / span * (len(_SPARK_CHARS) - 1))
        out.append(_SPARK_CHARS[max(0, min(level, len(_SPARK_CHARS) - 1))])
    return "".join(out)


def _candle_direction_row(candles: list[_Candle]) -> str:
    """Per-candle up/down row: '+' up, '-' down, '=' flat, '.' unknown."""
    out: list[str] = []
    for c in candles:
        o = _to_float(c.get("open"))
        cl = _to_float(c.get("close"))
        if o is None or cl is None:
            out.append(".")
            continue
        if cl > o:
            out.append("+")
        elif cl < o:
            out.append("-")
        else:
            out.append("=")
    return "".join(out)


def _price_ladder(closes: list[float], width: int = 40, height: int = 6) -> str:
    """Compact vertical ASCII chart: rows of '.' with '*' at sampled closes.

    Y-axis labels at the top (high) and bottom (low) of the observed range.
    """
    if not closes:
        return ""
    lo = min(closes)
    hi = max(closes)
    span = hi - lo
    rows: list[str] = []
    for row in range(height):
        level_hi = hi - span * (row / height)
        level_lo = hi - span * ((row + 1) / height)
        line: list[str] = []
        for i, v in enumerate(closes):
            if i >= width:
                break
            line.append("*" if level_lo <= v <= level_hi else ".")
        if row == 0:
            label = f"{hi:.8g}"
        elif row == height - 1:
            label = f"{lo:.8g}"
        else:
            label = ""
        rows.append(f"{label:>12} {''.join(line)}")
    return "\n".join(rows)


def _summary_text(analysis: dict[str, Any]) -> str:
    """Compact one-line metric digest (chat-friendly; the LLM renders it)."""
    rsi_value = analysis.get("rsi")
    rsi_part = f"RSI-14 {rsi_value:.1f}" if rsi_value is not None else "RSI-14 n/a"
    ema9 = analysis.get("ema9")
    ema21 = analysis.get("ema21")
    ema_part = f"EMA9 {ema9:.6g}/EMA21 {ema21:.6g}" if ema9 is not None and ema21 is not None else "EMA n/a"
    macd_part = f"MACD {analysis.get('macd_label') or 'n/a'}"
    return f"{rsi_part} · {ema_part} · {macd_part} · trend {analysis.get('trend') or 'NEUTRAL'}"


def _bias(analysis: dict[str, Any]) -> str:
    """Compact transparent bias from the real indicator digest."""
    score = 0
    trend = analysis.get("trend")
    if trend == "BULLISH":
        score += 1
    elif trend == "BEARISH":
        score -= 1
    rsi_value = analysis.get("rsi")
    if rsi_value is not None and rsi_value > 55:
        score += 1
    elif rsi_value is not None and rsi_value < 45:
        score -= 1
    return "bullish" if score >= 2 else "bearish" if score <= -2 else "neutral"


def _levels_line(analysis: dict[str, Any]) -> str:
    """Key price levels from the analysis (real candle values)."""
    parts: list[str] = []
    price = analysis.get("price")
    high = analysis.get("high")
    low = analysis.get("low")
    support = analysis.get("support")
    resistance = analysis.get("resistance")
    if price is not None:
        parts.append(f"last {price:.8g}")
    if high is not None:
        parts.append(f"high {high:.8g}")
    if low is not None:
        parts.append(f"low {low:.8g}")
    if support is not None:
        parts.append(f"support {support:.8g}")
    if resistance is not None:
        parts.append(f"resistance {resistance:.8g}")
    return " · ".join(parts)


def _render_chart(
    snapshot: dict[str, Any],
    timeframe: str,
    unit: str,
    candles: list[_Candle],
    analysis: dict[str, Any],
    closes: list[float],
    source: str,
    width: int,
    height: int,
) -> str:
    """Assemble the Telegram-monospace ASCII chart block (text only)."""
    sym = snapshot.get("symbol") or "?"
    chain = snapshot.get("chain") or ""
    header = f"${sym} ({chain}) — {timeframe} chart ({len(candles)} x {unit} candles)  [src: {source}]"
    lines = [
        header,
        _sparkline(closes, width),
        _candle_direction_row(candles),
        _price_ladder(closes, min(width, 40), height),
        _summary_text(analysis),
        _levels_line(analysis),
    ]
    return "\n".join(line for line in lines if line)


# ── Tool handler ───────────────────────────────────────────────────────────


async def chart(
    *,
    address: str = "",
    symbol: str = "",
    chain: str = "",
    timeframe: str = "24h",
    width: int = 48,
    height: int = 6,
) -> ToolResult:
    """Compact ASCII price-action chart + TA digest for a token address or
    ``$SYMBOL``.

    Resolves the pair (DexScreener), fetches OHLCV own-data first (backend
    RugCharts/ClickHouse -> GeckoTerminal), slices to the requested window
    (last 24 hourly candles for ``24h``, last 7 daily for ``7d``) and renders
    a text-only chart: ASCII sparkline, per-candle direction row, vertical
    price ladder and the TA digest (RSI-14 / EMA-9/21 / MACD /
    support-resistance) from the reused pure ``ta_indicators`` math.

    Args:
        address: Token contract address (EVM or Solana).
        symbol: ``$SYMBOL`` (used when no address is given).
        chain: Optional chain filter ("ethereum", "solana", "base", ...).
        timeframe: "24h" (24 hourly candles) or "7d" (7 daily candles).
        width: Max sparkline / ladder character width.
        height: Ladder rows.

    Returns:
        ToolResult with ``chart_text`` (the monospace block), ``sparkline``,
        ``candle_directions``, the indicator digest and a ``summary_text``.
        ``ok=False`` only when no pair can be resolved. When no candles are
        reachable the tool still returns ``ok=True`` with the REAL DexScreener
        snapshot and ``indicators_available=false`` — the chart is never
        fabricated.
    """
    tf = timeframe.strip().lower()
    if tf not in _TIMEFRAMES:
        tf = "24h"
    unit = _TIMEFRAMES[tf][0]
    gecko_unit = _TIMEFRAMES[tf][1]

    pair = await _resolve_pair(address, symbol, chain)
    if pair is None:
        return ToolResult(
            tool="chart",
            ok=False,
            error="could not resolve a trading pair for that symbol/address (DexScreener unreachable or no match)",
        )

    resolved_chain = _normalize_chain(chain or str(pair.get("chainId") or ""))
    pair_address = str(pair.get("pairAddress") or "")
    snapshot = _pair_snapshot(pair, address, symbol)

    candles, source = await _fetch_candles(resolved_chain, pair_address, unit, gecko_unit)
    candles = _slice_candles(candles, tf)

    if candles:
        analysis = analyze_candles(candles)
        if analysis is None:
            return ToolResult(
                tool="chart",
                ok=False,
                error="OHLCV candles returned but could not be parsed (malformed payload)",
            )
        closes = [float(c["close"]) for c in candles]
        chart_text = _render_chart(snapshot, tf, unit, candles, analysis, closes, source, width, height)
        return ToolResult(
            tool="chart",
            ok=True,
            data={
                "query": {"address": address, "symbol": symbol, "chain": resolved_chain},
                "symbol": snapshot["symbol"],
                "name": snapshot["name"],
                "chain": resolved_chain,
                "pair_address": pair_address,
                "timeframe": tf,
                "candle_unit": unit,
                "candles_shown": len(candles),
                "indicators_available": True,
                "chart_text": chart_text,
                "sparkline": _sparkline(closes, width),
                "candle_directions": _candle_direction_row(candles),
                "indicators": {
                    "price": analysis.get("price"),
                    "change_pct": analysis.get("change_pct"),
                    "rsi_14": analysis.get("rsi"),
                    "ema_9": analysis.get("ema9"),
                    "ema_21": analysis.get("ema21"),
                    "macd_label": analysis.get("macd_label"),
                    "support": analysis.get("support"),
                    "resistance": analysis.get("resistance"),
                    "trend": analysis.get("trend"),
                },
                "verdict": {"bias": _bias(analysis)},
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
    return ToolResult(
        tool="chart",
        ok=True,
        data={
            "query": {"address": address, "symbol": symbol, "chain": resolved_chain},
            "symbol": snapshot["symbol"],
            "name": snapshot["name"],
            "chain": resolved_chain,
            "pair_address": pair_address,
            "timeframe": tf,
            "candle_unit": unit,
            "candles_shown": 0,
            "indicators_available": False,
            "chart_text": f"No OHLCV candle history available right now — live snapshot: {momentum_text}",
            "sparkline": "",
            "candle_directions": "",
            "indicators": None,
            "snapshot": snapshot,
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
    reg.register("chart", chart, timeout=12.0, ttl=120.0)
