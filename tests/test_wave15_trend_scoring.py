"""Test wave 15 — trend_algo scoring engine (pure math).

Contracts read from source (2026-08-22). Pure functions only: no network,
no backend. Covers the transparent trend score, the risk-first sketch
score and the coercion helpers that moved into the package during the
de-godding split.
"""

from __future__ import annotations

import math

import pytest

from rugmunchbot.tools.trend_algo import (
    _clamp01,
    _exp_norm,
    _normalize_chain,
    _score_token,
    _sketch_score,
    _to_float,
    _to_int,
)

# ── coercion helpers ─────────────────────────────────────────────────────────


def test_clamp01_bounds() -> None:
    assert _clamp01(0.5) == 0.5
    assert _clamp01(-1.0) == 0.0
    assert _clamp01(2.0) == 1.0


def test_exp_norm_reference_and_values() -> None:
    assert _exp_norm(10.0, 0) == 0.0  # bad reference never divides
    assert _exp_norm(-5.0, 100.0) == 0.0  # negative clamped to zero
    assert _exp_norm(0.0, 100.0) == 0.0
    assert _exp_norm(100.0, 100.0) == pytest.approx(1 - math.exp(-1))


def test_to_float_to_int_coercion() -> None:
    assert _to_float("12.5") == 12.5
    assert _to_float(None) is None
    assert _to_float("junk") is None
    assert _to_int("2.9") == 2  # via float, truncated
    assert _to_int(None) is None
    assert _to_int("abc") is None


def test_normalize_chain_aliases() -> None:
    assert _normalize_chain("SOL") == "solana"
    assert _normalize_chain("bnb") == "bsc"
    assert _normalize_chain("ethereum") == "ethereum"
    assert _normalize_chain("unknown-chain") == "unknown-chain"
    assert _normalize_chain("") == "all"
    assert _normalize_chain("ALL") == "all"


# ── transparent trend score ──────────────────────────────────────────────────


def _base_metrics() -> dict[str, object]:
    return {
        "volume_24h": 1_000_000.0,
        "liquidity_usd": 250_000.0,
        "buys_24h": 600,
        "sells_24h": 400,
        "price_change_24h": 100.0,
        "age_hours": 36.0,
    }


def test_score_token_unscanned_full_breakdown() -> None:
    out = _score_token(_base_metrics(), None)
    # Every sub-score hits a known closed form for this fixture.
    assert out["components"] == {
        "volume_score": 63.2,  # 1-exp(-1)
        "liquidity_score": 63.2,
        "growth_score": 36.3,  # .5*(ratio term) + .5*(trade-count term)
        "momentum_score": 63.2,  # |100%| move vs 100% reference
        "age_score": 50.0,  # 36h of the 72h decay window
    }
    assert out["risk_penalty"] == 0.0
    assert out["score"] == pytest.approx(55.9, abs=0.11)


def test_score_token_risk_score_applies_partial_penalty() -> None:
    out = _score_token(_base_metrics(), {"risk_score": 75})
    assert out["risk_penalty"] == 30.0  # clamp01((75-50)/50) * 60
    assert out["score"] == pytest.approx(39.1, abs=0.11)


def test_score_token_flagged_tokens_score_zero() -> None:
    out = _score_token(_base_metrics(), {"is_honeypot": True})
    assert out["risk_penalty"] == 100.0
    assert out["score"] == 0.0


def test_score_token_defaults_missing_fields() -> None:
    out = _score_token({}, None)
    assert out["components"]["volume_score"] == 0.0
    assert out["components"]["age_score"] == 50.0  # unknown age -> neutral 0.5
    assert out["score"] >= 0.0


# ── risk-first sketch score ──────────────────────────────────────────────────


def test_sketch_score_unscanned_proxies_and_flags() -> None:
    out = _sketch_score(
        {"liquidity_usd": 1000.0, "price_change_24h": -80.0, "age_hours": 10.0},
        None,
    )
    assert out["flags"] == ["unscanned", "thin_liquidity", "violent_moves", "fresh_launch"]
    # Thin liquidity is the DOMINANT proxy in a risk-first score: a $1k
    # exit adds ~25 of the 48.8 total, plus violent-move and fresh-launch.
    assert out["sketch_score"] == pytest.approx(48.8, abs=0.3)


def test_sketch_score_honeypot_dominates() -> None:
    out = _sketch_score(
        {"liquidity_usd": 500_000.0, "price_change_24h": 10.0, "age_hours": 200.0},
        {"is_honeypot": True, "risk_score": 90},
    )
    assert out["flags"] == ["honeypot", "risk_score=90/100"]
    assert out["sketch_score"] == pytest.approx(195.3, abs=0.3)


def test_sketch_score_clean_scanned_token_is_low() -> None:
    out = _sketch_score(
        {"liquidity_usd": 500_000.0, "price_change_24h": 5.0, "age_hours": 200.0},
        {"risk_score": 10},
    )
    assert out["flags"] == ["sketchy_proxies"]
    # Raw scanner risk_score counts point-for-point (10) plus small
    # liquidity/momentum proxies; old age adds nothing.
    assert out["sketch_score"] == pytest.approx(14.4, abs=0.3)


def test_sketch_score_caps_at_200() -> None:
    out = _sketch_score(
        {"liquidity_usd": 100.0, "price_change_24h": -300.0, "age_hours": 1.0},
        {"is_honeypot": True, "is_rug_pull": True, "risk_score": 100},
    )
    assert out["sketch_score"] == 200.0
    assert set(out["flags"]) >= {"honeypot", "rug_pull", "thin_liquidity"}
