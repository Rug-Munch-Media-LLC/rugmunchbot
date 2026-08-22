"""trend_algo scoring — transparent trend score + risk-first sketch score.

Extracted verbatim from the former single-module ``tools/trend_algo.py``.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.tools.trend_algo._constants import (
    _AGE_DECAY_HOURS,
    _LIQ_REF_USD,
    _MOMENTUM_REF_PCT,
    _RISK_FLOOR,
    _RISK_MAX_PENALTY,
    _TRADES_REF,
    _VOL_REF_USD,
    _W_AGE,
    _W_GROWTH,
    _W_LIQ,
    _W_MOMENTUM,
    _W_VOL,
)
from rugmunchbot.tools.trend_algo._normalize import _clamp01, _exp_norm


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
    s_age = (
        (1.0 - min(max(float(age_h), 0.0) / _AGE_DECAY_HOURS, 1.0)) if age_h is not None else 0.5
    )

    if risk is not None and (risk.get("is_honeypot") or risk.get("is_rug_pull")):
        risk_penalty = 1.0
    elif risk is not None and risk.get("risk_score") is not None:
        risk_score = float(risk["risk_score"])
        risk_penalty = (
            _clamp01((risk_score - _RISK_FLOOR) / (100.0 - _RISK_FLOOR)) * _RISK_MAX_PENALTY
        )
    else:
        risk_penalty = 0.0

    raw = (
        _W_VOL * s_vol
        + _W_LIQ * s_liq
        + _W_GROWTH * s_growth
        + _W_MOMENTUM * s_momentum
        + _W_AGE * s_age
    )
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
