# VENDORED 2026-08-17 from rmi-backend/app/osint/dex_activity/_signals.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""Wash-trading signal heuristics for the DEX-activity detector.

Each signal inspects one DexScreener pair dict and returns zero or more
:class:`~app.osint.OsintFinding` objects (``source="dexscreener"``,
``category="wash_trading"``):

* **volume divergence** — 24h volume with little or no backing liquidity.
* **buy/sell symmetry** — near-equal buy and sell counts (round-trip pattern).
* **liquidity churn** — LP add/remove proxy from liquidity state + turnover +
  pair age (the free API exposes no historical LP events).
* **trader concentration** — few trades moving a lot of volume (unique-trader
  counts are not exposed; inferred from average trade size + txn count).

Aggregation helpers (:func:`_signal_score`, :func:`_risk_level`,
:func:`_analyze_pair_signals`, :func:`_context_finding`) live here too so the
public entry point in :mod:`app.osint.dex_activity._core` stays thin.
"""

from __future__ import annotations

from typing import Any, Final

from rugmunchbot._vendor.osint import OsintFinding
from rugmunchbot._vendor.osint.dex_activity._common import (
    _fmt_usd,
    _num,
    _pair_age_hours,
    _subdict,
    _token_symbol,
)

# ─── Tunables ────────────────────────────────────────────────────────

#: Minimum 24h txn count before buy/sell symmetry is statistically meaningful.
MIN_TXNS_FOR_SYMMETRY: Final = 30
#: Volume:liquidity turnover thresholds (24h volume / USD liquidity).
VOLUME_RATIO_LOW: Final = 8.0
VOLUME_RATIO_MEDIUM: Final = 15.0
VOLUME_RATIO_HIGH: Final = 30.0
#: Buy/sell symmetry (min(buys, sells) / max(buys, sells)) thresholds.
SYMMETRY_LOW: Final = 0.88
SYMMETRY_MEDIUM: Final = 0.93
SYMMETRY_HIGH: Final = 0.97
#: Established-pair (>= 48h old) churn thresholds.
CHURN_RATIO_MEDIUM: Final = 30.0
CHURN_RATIO_HIGH: Final = 60.0
#: Young-pair churn thresholds (hours since creation and volume:liquidity ratio).
YOUNG_PAIR_HOURS: Final = 48.0
YOUNG_PAIR_CHURN_RATIO: Final = 10.0
YOUNG_PAIR_CHURN_HIGH: Final = 20.0
#: Average trade size (24h volume / 24h txns) concentration thresholds.
AVG_TRADE_MEDIUM: Final = 10_000.0
AVG_TRADE_HIGH: Final = 50_000.0
#: Few-trades-large-volume concentration thresholds.
FEW_TRADES_COUNT: Final = 20
FEW_TRADES_VOLUME: Final = 100_000.0
#: Aggregate risk score cutoffs (high=2, medium=1, low=0.5 per finding).
RISK_HIGH_SCORE: Final = 3.0
RISK_MEDIUM_SCORE: Final = 1.0

_CONFIDENCE_POINTS: Final[dict[str, float]] = {"high": 2.0, "medium": 1.0, "low": 0.5}


# ─── Signals (one per wash-trading heuristic) ────────────────────────


def _signal_volume_divergence(pair: dict[str, Any]) -> list[OsintFinding]:
    """Volume/liquidity divergence — 24h volume with little or no backing liquidity."""
    volume = _num(_subdict(pair, "volume").get("h24"))
    liquidity = _num(_subdict(pair, "liquidity").get("usd"))
    findings: list[OsintFinding] = []

    if liquidity <= 0:
        if volume > 0:
            findings.append(
                OsintFinding(
                    source="dexscreener",
                    category="wash_trading",
                    summary=f"nonzero 24h volume {_fmt_usd(volume)} with zero liquidity",
                    confidence="high",
                    data={"signal": "volume_divergence", "volume_h24_usd": volume, "liquidity_usd": 0.0},
                )
            )
        return findings

    ratio = volume / liquidity
    if ratio >= VOLUME_RATIO_HIGH:
        summary = (
            f"extreme volume:liquidity turnover {ratio:.1f}x (24h vol {_fmt_usd(volume)} vs liq {_fmt_usd(liquidity)})"
        )
        confidence = "high"
    elif ratio >= VOLUME_RATIO_MEDIUM:
        summary = (
            f"high volume:liquidity turnover {ratio:.1f}x (24h vol {_fmt_usd(volume)} vs liq {_fmt_usd(liquidity)})"
        )
        confidence = "medium"
    elif ratio >= VOLUME_RATIO_LOW:
        summary = (
            f"elevated volume:liquidity turnover {ratio:.1f}x (24h vol {_fmt_usd(volume)} vs liq {_fmt_usd(liquidity)})"
        )
        confidence = "low"
    else:
        return findings

    findings.append(
        OsintFinding(
            source="dexscreener",
            category="wash_trading",
            summary=summary,
            confidence=confidence,
            data={
                "signal": "volume_divergence",
                "volume_h24_usd": volume,
                "liquidity_usd": liquidity,
                "ratio": round(ratio, 2),
            },
        )
    )
    return findings


def _signal_buy_sell_symmetry(pair: dict[str, Any]) -> list[OsintFinding]:
    """Buy/sell symmetry — near-equal buy/sell counts (round-trip pattern)."""
    h24 = _subdict(_subdict(pair, "txns"), "h24")
    buys = _num(h24.get("buys"))
    sells = _num(h24.get("sells"))
    total = buys + sells
    if total < MIN_TXNS_FOR_SYMMETRY:
        return []

    symmetry = min(buys, sells) / max(buys, sells)
    if symmetry >= SYMMETRY_HIGH:
        summary = f"near-perfect buy/sell symmetry {symmetry:.3f} ({buys:.0f} buys vs {sells:.0f} sells in 24h) — round-trip signature"
        confidence = "high"
    elif symmetry >= SYMMETRY_MEDIUM:
        summary = f"high buy/sell symmetry {symmetry:.3f} ({buys:.0f} buys vs {sells:.0f} sells in 24h)"
        confidence = "medium"
    elif symmetry >= SYMMETRY_LOW:
        summary = f"elevated buy/sell symmetry {symmetry:.3f} ({buys:.0f} buys vs {sells:.0f} sells in 24h)"
        confidence = "low"
    else:
        return []

    return [
        OsintFinding(
            source="dexscreener",
            category="wash_trading",
            summary=summary,
            confidence=confidence,
            data={"signal": "buy_sell_symmetry", "buys_h24": buys, "sells_h24": sells, "symmetry": round(symmetry, 3)},
        )
    ]


def _signal_liquidity_churn(pair: dict[str, Any]) -> list[OsintFinding]:
    """Liquidity churn/volatility — LP add/remove proxy from liquidity state + turnover + age.

    The free DexScreener API does not expose historical LP events; churn is
    inferred from: zero liquidity with volume (liquidity pulled or never
    added), extreme turnover on a young pair, and extreme sustained turnover
    on an established pair.
    """
    volume = _num(_subdict(pair, "volume").get("h24"))
    liquidity = _num(_subdict(pair, "liquidity").get("usd"))
    age_hours = _pair_age_hours(pair)
    findings: list[OsintFinding] = []

    if liquidity <= 0:
        if volume > 0:
            findings.append(
                OsintFinding(
                    source="dexscreener",
                    category="wash_trading",
                    summary="no liquidity backing the pair while volume trades — LP removed or never added",
                    confidence="high",
                    data={
                        "signal": "liquidity_churn",
                        "proxy": "no LP history in public API",
                        "liquidity_usd": 0.0,
                        "volume_h24_usd": volume,
                    },
                )
            )
        return findings

    ratio = volume / liquidity
    if age_hours is not None and age_hours < YOUNG_PAIR_HOURS:
        if ratio >= YOUNG_PAIR_CHURN_HIGH:
            summary = (
                f"extreme turnover {ratio:.1f}x on a {age_hours:.0f}h-old pair "
                f"({_fmt_usd(volume)} volume vs {_fmt_usd(liquidity)} liquidity) — consistent with LP churn/wash"
            )
            confidence = "high"
        elif ratio >= YOUNG_PAIR_CHURN_RATIO:
            summary = (
                f"high turnover {ratio:.1f}x on a {age_hours:.0f}h-old pair "
                f"({_fmt_usd(volume)} volume vs {_fmt_usd(liquidity)} liquidity)"
            )
            confidence = "medium"
        else:
            return findings
    elif ratio >= CHURN_RATIO_HIGH:
        summary = f"extreme sustained turnover {ratio:.1f}x — liquidity churned repeatedly (LP add/remove proxy)"
        confidence = "high"
    elif ratio >= CHURN_RATIO_MEDIUM:
        summary = f"sustained high turnover {ratio:.1f}x — liquidity churned repeatedly (LP add/remove proxy)"
        confidence = "medium"
    else:
        return findings

    findings.append(
        OsintFinding(
            source="dexscreener",
            category="wash_trading",
            summary=summary,
            confidence=confidence,
            data={
                "signal": "liquidity_churn",
                "proxy": "no LP history in public API",
                "volume_h24_usd": volume,
                "liquidity_usd": liquidity,
                "ratio": round(ratio, 2),
                "pair_age_hours": round(age_hours, 1) if age_hours is not None else None,
            },
        )
    )
    return findings


def _signal_trader_concentration(pair: dict[str, Any]) -> list[OsintFinding]:
    """Trader concentration — few trades moving a lot of volume.

    The free DexScreener API does not expose unique-trader counts; this is a
    proxy: a large average trade size (24h volume / 24h txn count), or a
    handful of trades moving significant volume, implies activity concentrated
    in a small set of actors.
    """
    volume = _num(_subdict(pair, "volume").get("h24"))
    h24 = _subdict(_subdict(pair, "txns"), "h24")
    total_txns = _num(h24.get("buys")) + _num(h24.get("sells"))
    if volume <= 0 or total_txns <= 0:
        return []

    avg_trade = volume / total_txns
    if avg_trade >= AVG_TRADE_HIGH:
        summary = (
            f"average trade size {_fmt_usd(avg_trade)} across {total_txns:.0f} trades in 24h — "
            "activity concentrated in very few wallets"
        )
        confidence = "high"
    elif avg_trade >= AVG_TRADE_MEDIUM:
        summary = (
            f"average trade size {_fmt_usd(avg_trade)} across {total_txns:.0f} trades in 24h — "
            "activity concentrated in few wallets"
        )
        confidence = "medium"
    elif total_txns < FEW_TRADES_COUNT and volume >= FEW_TRADES_VOLUME:
        summary = (
            f"only {total_txns:.0f} trades moved {_fmt_usd(volume)} in 24h — "
            "activity concentrated in a small set of wallets"
        )
        confidence = "low"
    else:
        return []

    return [
        OsintFinding(
            source="dexscreener",
            category="wash_trading",
            summary=summary,
            confidence=confidence,
            data={
                "signal": "trader_concentration",
                "proxy": "no unique-trader counts in public API",
                "volume_h24_usd": volume,
                "txns_h24": total_txns,
                "avg_trade_usd": round(avg_trade, 2),
            },
        )
    ]


# ─── Analysis ────────────────────────────────────────────────────────


def _analyze_pair_signals(pair: dict[str, Any]) -> list[OsintFinding]:
    """Run every wash-trading signal against one pair."""
    findings: list[OsintFinding] = []
    for signal in (
        _signal_volume_divergence,
        _signal_buy_sell_symmetry,
        _signal_liquidity_churn,
        _signal_trader_concentration,
    ):
        findings.extend(signal(pair))
    return findings


def _signal_score(findings: list[OsintFinding]) -> float:
    """Aggregate risk score from signal confidence levels (high=2, medium=1, low=0.5)."""
    return sum(_CONFIDENCE_POINTS.get(f.confidence, 0.0) for f in findings)


def _risk_level(score: float) -> str:
    """Map an aggregate score to ``high``/``medium``/``low``."""
    if score >= RISK_HIGH_SCORE:
        return "high"
    if score >= RISK_MEDIUM_SCORE:
        return "medium"
    return "low"


def _context_finding(pair: dict[str, Any]) -> OsintFinding:
    """Context finding describing the pair (not scored; mirrors wallet.py's chain finding)."""
    base = _token_symbol(pair.get("baseToken"))
    quote = _token_symbol(pair.get("quoteToken"))
    chain = str(pair.get("chainId") or "?")
    dex = str(pair.get("dexId") or "?")
    age_hours = _pair_age_hours(pair)
    return OsintFinding(
        source="dexscreener",
        category="pair_context",
        summary=f"{base}/{quote} on {dex} ({chain})",
        confidence="high",
        data={
            "pair_address": pair.get("pairAddress") or "",
            "chain_id": chain,
            "dex_id": dex,
            "base_token": base,
            "quote_token": quote,
            "price_usd": _num(pair.get("priceUsd")),
            "liquidity_usd": _num(_subdict(pair, "liquidity").get("usd")),
            "volume_h24_usd": _num(_subdict(pair, "volume").get("h24")),
            "buys_h24": _num(_subdict(_subdict(pair, "txns"), "h24").get("buys")),
            "sells_h24": _num(_subdict(_subdict(pair, "txns"), "h24").get("sells")),
            "pair_age_hours": round(age_hours, 1) if age_hours is not None else None,
        },
    )
