"""Tool-result prompt rendering — signal formatting, compact lists."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


_USD_FIELDS = frozenset(
    {"price_usd", "market_cap", "mcap", "fdv", "liquidity_usd", "volume_24h", "window_high", "window_low"}
)


_PCT_FIELDS = frozenset(
    {
        "price_change_24h",
        "change_24h",
        "change_1h",
        "change_6h",
        "change_window_pct",
        "distance_from_window_high_pct",
        "distance_from_window_low_pct",
    }
)


def _fmt_scaled(v: float, unit: str) -> str:
    """``v`` (already divided by the scale) + unit, up to 2 decimals,
    trailing zeros stripped: 1.20 -> '1.2', 840.00 -> '840', 12.50 -> '12.5'."""
    return f"{v:.2f}".rstrip("0").rstrip(".") + unit


def _fmt_usd(v: float) -> str:
    """Compact USD: $1.2M / $840K / $3.14 / $0.0042 / $0.0000123."""
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e12:
        return f"{sign}${_fmt_scaled(a / 1e12, 'T')}"
    if a >= 1e9:
        return f"{sign}${_fmt_scaled(a / 1e9, 'B')}"
    if a >= 1e6:
        return f"{sign}${_fmt_scaled(a / 1e6, 'M')}"
    if a >= 1e3:
        return f"{sign}${_fmt_scaled(a / 1e3, 'K')}"
    if a >= 1:
        return f"{sign}${a:,.2f}"
    if a >= 0.01:
        return f"{sign}${a:.4f}"
    if a > 0:
        # Expand tiny prices to a plain decimal the model can read literally
        # (never scientific notation like 1.23e-05).
        return f"{sign}${a:.10f}".rstrip("0").rstrip(".")
    return "$0"


def _fmt_signal(key: str, value: Any) -> str:
    """Format one signal value for the LLM prompt; strings/bools pass through.

    Only USD fields (price/mcap/liquidity/volume/fdv) and percentage fields
    are reformatted; every other signal keeps its raw value.
    """
    if isinstance(value, bool) or value is None or not isinstance(value, (int, float)):
        return str(value)
    v = float(value)
    if key in _USD_FIELDS:
        return _fmt_usd(v)
    if key in _PCT_FIELDS:
        return "0.0%" if v == 0 else f"{v:+.1f}%"
    return str(value)


def _compact_list(value: list[object], cap: int = 10) -> str:
    """Render a list payload as compact lines (one per item when possible).

    ``trending`` / ``trend_by_chain`` return token lists as list-of-dicts;
    the old ``str(value)[:500]`` truncation cut mid-JSON and hid the fields
    the LLM must cite (risk_flags / sketch_score / score). Each dict item is
    projected onto its signal fields; non-dict items fall back to str().

    Signal fields mirror the ACTUAL payload keys produced by
    ``tools/trend_algo.py``, ``tools/price.py`` and ``tools/security.py``
    (both canonical and legacy spellings) so the 4B sees the concrete
    numbers it must quote: price, mcap, liquidity, holders, age, buy/sell
    split, risk score/flags — not just symbol + change. Numeric fields are
    human-formatted via :func:`_fmt_signal` (``$0.0000123`` / ``$1.2M`` /
    ``+12.3%``) so the model never misreads raw floats or scientific
    notation.
    """
    if not value:
        return "[]"
    out: list[str] = []
    for item in value[:cap]:
        if isinstance(item, dict):
            keys = (
                "rank",
                "symbol",
                "name",
                "chain",
                "address",
                "price_usd",
                "price_change_24h",
                "change_24h",
                "volume_24h",
                "liquidity_usd",
                "market_cap",
                "mcap",
                "fdv",
                "holders",
                "holder_count",
                "age_hours",
                "age_days",
                "buys_24h",
                "sells_24h",
                "buy_ratio_24h",
                "score",
                "sketch_score",
                "risk_score",
                "risk_source",
                "risk_flags",
                "is_honeypot",
                "is_mintable",
                "is_rug_pull",
                "deployer_funded_by",
                "dex",
            )
            parts = [f"{k}={_fmt_signal(k, item[k])}" for k in keys if item.get(k) is not None]
            out.append("{" + ", ".join(parts) + "}")
        else:
            out.append(str(item)[:200])
    if len(value) > cap:
        out.append(f"... {len(value) - cap} more")
    return "\n      ".join(out)


def build_tool_prompt_block(results: Mapping[str, object]) -> str:
    """Render tool results as a compact LIVE DATA block for the LLM prompt.

    Only REAL tool data is included (ok=True). The block labels the source
    so the model attributes and never presents tool data as its own memory.
    ``Mapping`` (covariant) so callers can pass ``dict[str, ToolResult]``.
    """
    lines: list[str] = ["LIVE MARKET DATA (real, fetched just now):"]
    for name in sorted(results):
        result = results[name]
        if getattr(result, "ok", False) is False:
            continue
        from rugmunchbot.tools.registry import ToolResult

        if not isinstance(result, ToolResult):
            continue
        source = result.source or name
        lines.append(f"- {name} [source: {source}]:")
        payload = result.data
        if isinstance(payload, dict):
            for k, v in payload.items():
                if isinstance(v, list):
                    lines.append(f"    {k}: {_compact_list(v)}")
                elif isinstance(v, dict):
                    # Compact key=value lines with the same number formatting
                    # as _compact_list (a flat security/wallet payload is a
                    # dict, not a list — price_usd/liquidity_usd/volume_24h
                    # must render human-readable there too). Nested dicts fall
                    # back to compact JSON.
                    inner_parts = [
                        f"{ik}={_fmt_signal(ik, iv)}"
                        if not isinstance(iv, (dict, list))
                        else (f"{ik}={json.dumps(iv, separators=(',', ':'), ensure_ascii=False)[:200]}")
                        for ik, iv in v.items()
                    ]
                    lines.append(f"    {k}: {{{', '.join(inner_parts)}}}")
                else:
                    # Scalars: human-format known numeric signals (a flat
                    # security/wallet payload carries price_usd / risk_score /
                    # holders at top level), else plain str().
                    text = _fmt_signal(k, v)
                    lines.append(f"    {k}: {text[:500]}")
        else:
            lines.append(f"    {str(payload)[:800]}")
    lines.append(
        "You may ONLY cite symbols, addresses, prices, and metrics that appear in this LIVE MARKET DATA. If the user asks for data not present here, say you don't have live data for it."
    )
    return "\n".join(lines)
