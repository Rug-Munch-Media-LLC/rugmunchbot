"""Pure formatting / merge helpers for the scanning command handlers (degod).

Extracted from ``handlers/scanning.py``: DexScreener pair normalization,
completed-scan merge logic, TA card rendering (``format_ta_card`` + emoji
helpers), and the deployer risk-bucket emoji map. No network I/O — only
``config`` / ``formatters`` formatting helpers.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import fmt_chain, fmt_number, fmt_pct
from app.domains.telegram.rugmunchbot.formatters import footer_links, sep, thin_sep


def _ds_pairs(data: Any) -> list[dict[str, Any]]:
    """Normalize DexScreener token responses (dict ``{"pairs": [...]}`` or
    bare list — the API has served both shapes) into a pair list."""
    if isinstance(data, dict):
        pairs = data.get("pairs") or []
    elif isinstance(data, list):
        pairs = data
    else:
        pairs = []
    return [p for p in pairs if isinstance(p, dict)]


def _ds_created_iso(pair: dict[str, Any]) -> str | None:
    """DexScreener pair creation time (``pairCreatedAt``, ms epoch) as ISO.

    Returns None when absent — callers treat it as 'creation unknown'.
    """
    ms = pair.get("pairCreatedAt")
    try:
        ms = int(ms or 0)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.UTC).isoformat()


def _merge_completed_scan(result_v2: dict[str, Any], completed: dict[str, Any]) -> None:
    """Merge late-arriving background-phase data into the fast-path v2 payload.

    Only fills keys the fast path left empty — never overwrites. The status
    payload uses the legacy serializer (``deep_profile`` nesting,
    ``funding_analysis`` top-level) so both shapes are probed.
    """
    for key in ("wallet_metrics", "bundle_analysis"):
        if completed.get(key) and not result_v2.get(key):
            result_v2[key] = completed[key]
    # funding_analysis is consumed from evidence by v2_token_to_legacy —
    # fill both slots so downstream readers always find it.
    if completed.get("funding_analysis"):
        if not result_v2.get("funding_analysis"):
            result_v2["funding_analysis"] = completed["funding_analysis"]
        ev0 = result_v2.setdefault("evidence", {})
        if not ev0.get("funding_analysis"):
            ev0["funding_analysis"] = completed["funding_analysis"]
    if not result_v2.get("deployer_profile"):
        dp = completed.get("deployer_profile") or (completed.get("deep_profile") or {}).get("deployer_profile")
        if dp:
            result_v2["deployer_profile"] = dp
    completed_ev = completed.get("evidence")
    if isinstance(completed_ev, dict):
        ev = result_v2.setdefault("evidence", {})
        for k, v in completed_ev.items():
            if v and not ev.get(k):
                ev[k] = v


def _ta_arrow(trend: str) -> str:
    return {"BULLISH": "\U0001f7e2", "BEARISH": "\U0001f534", "NEUTRAL": "\u26aa"}.get(trend, "\u26aa")


def _ta_rsi_emoji(label: str) -> str:
    return {"Overbought": "\U0001f534", "Oversold": "\U0001f7e2", "Neutral": "\u26aa"}.get(label, "\u26aa")


def _ta_vol_arrow(trend: str) -> str:
    return {"Rising": "\U0001f4c8", "Declining": "\U0001f4c9", "Steady": "\u2796"}.get(trend, "\u2796")


def format_ta_card(analysis: dict[str, Any], meta: dict[str, Any]) -> str:
    """Render the /ta technical-analysis card (Telegram HTML).

    ``analysis`` comes from :func:`ta_indicators.analyze_candles`; ``meta``
    carries the DexScreener pair + RugCharts intel overlay:

        name, symbol, address, chain, price, change_24h,
        rug_score (int), rug_level (str)

    Follows the bot card conventions: sep()/thin_sep() dividers, bold
    labels, code-blocked address, footer_links().
    """
    name = meta.get("name") or "Unknown"
    symbol = meta.get("symbol") or "???"
    address = meta.get("address") or ""
    chain = meta.get("chain") or ""
    price = meta.get("price")
    change_24h = meta.get("change_24h")

    trend = analysis.get("trend") or "NEUTRAL"
    rsi_val = analysis.get("rsi")
    rsi_label = analysis.get("rsi_label") or "N/A"
    ema9 = analysis.get("ema9")
    ema21 = analysis.get("ema21")
    ema_cross = analysis.get("ema_cross") or "NEUTRAL"
    macd_line = analysis.get("macd_line")
    macd_label = analysis.get("macd_label") or "N/A"
    vol_ratio = analysis.get("volume_ratio")
    vol_trend = analysis.get("volume_trend") or "Steady"
    support = analysis.get("support")
    resistance = analysis.get("resistance")

    # Price header
    price_line = f"\U0001f4b0 Price: <b>{fmt_number(price)}</b>" if price is not None else ""
    if change_24h is not None:
        arrow = "\U0001f7e2" if change_24h >= 0 else "\U0001f534"
        price_line += f"  {arrow} {fmt_pct(change_24h)} 24h"

    lines = [
        "\U0001f4ca <b>Technical Analysis</b>",
        sep(),
        f"<b>{name}</b> ({symbol})",
        f"<code>{address}</code>" if address else "",
        f"{fmt_chain(chain)} \u00b7 1h \u00b7 {analysis.get('candles', 0)} candles",
        "",
        f"\U0001f4c8 <b>Trend: {trend}</b> {_ta_arrow(trend)}",
        thin_sep(),
        price_line,
    ]

    # Indicators
    if rsi_val is not None:
        lines.append(f"RSI-14: <b>{rsi_val:.1f}</b> {_ta_rsi_emoji(rsi_label)} {rsi_label}")
    else:
        lines.append("RSI-14: N/A")
    if ema9 is not None and ema21 is not None:
        lines.append(f"EMA-9: {fmt_number(ema9)} | EMA-21: {fmt_number(ema21)}")
        cross_emoji = (
            "\U0001f7e2" if ema_cross == "BULLISH CROSS" else "\U0001f534" if ema_cross == "BEARISH CROSS" else "\u26aa"
        )
        lines.append(f"EMA: {cross_emoji} <b>{ema_cross}</b>")
    if macd_line is not None:
        macd_emoji = "\U0001f7e2" if macd_label == "Bullish" else "\U0001f534" if macd_label == "Bearish" else "\u26aa"
        lines.append(f"MACD: {macd_emoji} <b>{macd_label}</b> ({macd_line:+.8g})")
    if vol_ratio is not None:
        lines.append(f"Volume: {_ta_vol_arrow(vol_trend)} {vol_ratio:.1f}\u00d7 avg ({vol_trend})")
    else:
        lines.append(f"Volume: {_ta_vol_arrow(vol_trend)} {vol_trend}")

    # Key levels
    lines += [
        "",
        "\U0001f3af <b>Key Levels</b>",
        thin_sep(),
    ]
    if resistance is not None:
        lines.append(f"Resistance: {fmt_number(resistance)}")
    if support is not None:
        lines.append(f"Support:    {fmt_number(support)}")

    # Risk overlay from RugCharts intel
    rug_score = meta.get("rug_score")
    if rug_score is not None:
        rug_level = (meta.get("rug_level") or "UNKNOWN").upper()
        level_emoji = {"SAFE": "\U0001f7e2", "CAUTION": "\U0001f7e1", "DANGER": "\U0001f534"}.get(rug_level, "\u26aa")
        lines += [
            "",
            f"\U0001f6e1\ufe0f <b>Rug Risk</b>: {level_emoji} {rug_level} \u2014 {int(rug_score)}/100",
        ]

    lines += ["", "<i>TA is not financial advice. Always /scan for security.</i>"]
    lines.append(footer_links())
    return "\n".join(line for line in lines if line != "")


# ── /deployer — quick deployer fingerprint check ──
_DEPLOYER_BUCKET_EMOJI = {
    "NEW": "\U0001f195",  # 🆕
    "REPEAT_CLEAN": "\U0001f7e2",  # 🟢
    "REPEAT_RUG": "\U0001f534",  # 🔴
    "FARM": "\U0001f331",  # 🌱
    "PROLIFIC": "\U0001f4b0",  # 💰
}


def _bucket_emoji(bucket: str) -> str:
    return _DEPLOYER_BUCKET_EMOJI.get(bucket.upper(), "\U0001f3f7\ufe0f")
