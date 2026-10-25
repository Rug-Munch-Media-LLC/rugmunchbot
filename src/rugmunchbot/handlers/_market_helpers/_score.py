"""Per-token score line for trending cards — reuses the trend_algo formula.

The number is the existing ``trend_algo`` trend score (weighted volume,
liquidity, growth, momentum and age, de-rated by the scanner risk score).
It is NOT re-derived here: this module maps a trending token dict onto that
scorer's inputs and formats the "why" line, so a card shows its drivers
instead of an unexplained score.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.handlers._market_helpers._tokens import _token_age_hours
from rugmunchbot.tools.trend_algo._scoring import _score_token

# (short label, component key) — order is the breakdown order on the card.
_COMPONENTS = (
    ("vol", "volume_score"),
    ("liq", "liquidity_score"),
    ("grw", "growth_score"),
    ("mom", "momentum_score"),
    ("age", "age_score"),
)
_DRIVER_LABEL = {
    "volume_score": "volume",
    "liquidity_score": "liquidity",
    "growth_score": "buy pressure",
    "momentum_score": "momentum",
    "age_score": "freshness",
}


def _score_metrics(token: dict[str, Any]) -> dict[str, Any]:
    """Map a trending token dict onto the trend_algo metric inputs."""
    return {
        "volume_24h": token.get("volume_24h") or 0.0,
        "liquidity_usd": token.get("liquidity_usd") or 0.0,
        "buys_24h": token.get("buys_24h") or 0,
        "sells_24h": token.get("sells_24h") or 0,
        "price_change_24h": token.get("change_24h") or 0.0,
        "age_hours": _token_age_hours(token),
    }


def _score_line(token: dict[str, Any]) -> str:
    """HTML score line: score, top driver and the component breakdown."""
    risk = {
        "risk_score": token.get("risk_score"),
        "is_honeypot": token.get("is_honeypot"),
        "is_rug_pull": token.get("is_rug_pull"),
    }
    scored = _score_token(_score_metrics(token), risk)
    components = scored["components"]
    best_key = max(components, key=lambda key: components[key])
    breakdown = " · ".join(f"{label} {components[key]:.0f}" for label, key in _COMPONENTS)
    line = (
        f"\U0001f4ca Score <b>{scored['score']:.1f}</b> · "
        f"driver: {_DRIVER_LABEL[best_key]} · {breakdown}"
    )
    penalty = scored["risk_penalty"]
    if penalty > 0:
        line += f" · risk \u2212{penalty:.0f}%"
    return line
