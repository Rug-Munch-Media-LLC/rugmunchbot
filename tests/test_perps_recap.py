"""Perps desk recap lane — CTA verification, card discipline, fail-open."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from rugmunchbot.channels.perps_card import render_perps_card
from rugmunchbot.channels.perps_recap import build_perps_recap_post
from rugmunchbot.referrals import DISCLOSURE, Referral, gmgn_perps_referral
from rugmunchbot.referrals._tiers import STANDARD


class _Result:
    """Minimal ToolResult stand-in (tool, ok, data)."""

    def __init__(self, data):
        self.data = data
        self.ok = True


def _rows():
    return [
        {
            "symbol": "BTC",
            "oi_usd": 1.42e10,
            "near_side": "LONGS",
            "trigger_price": 61240.0,
            "distance_pct": 3.1,
            "funding_rate": 0.0001,
            "funding_venue": "hyperliquid",
        },
        {
            "symbol": "ETH",
            "oi_usd": 6.1e9,
            "near_side": "SHORTS",
            "trigger_price": 2700.0,
            "distance_pct": 1.4,
            "funding_rate": 0.0002,
            "funding_venue": "binance",
        },
        {
            "symbol": "SOL",
            "oi_usd": 2.2e9,
            "near_side": "LONGS",
            "trigger_price": 168.0,
            "distance_pct": 5.6,
            "funding_rate": None,
            "funding_venue": None,
        },
    ]


# ── referral / CTA verification ──────────────────────────────────────────────


def test_verified_market_builds_referral_url(monkeypatch):
    monkeypatch.setenv("RMI_REF_GMGN_CODE", "rugmunch")
    monkeypatch.delenv("RMI_REF_GMGN", raising=False)
    ref = gmgn_perps_referral("btc")
    assert ref.available
    assert ref.url == "https://gmgn.ai/perpetual/BTC.PERP_USDC?ref=rugmunch"


def test_unverified_market_is_null_with_reason(monkeypatch):
    monkeypatch.setenv("RMI_REF_GMGN_CODE", "rugmunch")
    ref = gmgn_perps_referral("DOGE")
    assert ref.url is None
    assert ref.available is False
    assert ref.reason is not None
    assert "unverified-market" in ref.reason


def test_unconfigured_referral_is_null_with_reason(monkeypatch):
    monkeypatch.delenv("RMI_REF_GMGN", raising=False)
    monkeypatch.delenv("RMI_REF_GMGN_CODE", raising=False)
    ref = gmgn_perps_referral("ETH")
    assert ref.url is None
    assert ref.reason == "gmgn-perps:referral-not-configured"


def test_non_https_override_rejected(monkeypatch):
    monkeypatch.setenv("RMI_REF_GMGN", "http://gmgn.ai/perpetual/BTC.PERP_USDC")
    ref = gmgn_perps_referral("BTC")
    assert ref.url is None
    assert ref.reason == "gmgn-perps:invalid-url"


# ── card discipline: one link, disclaimer, disclosure ────────────────────────


def test_card_has_exactly_one_link_and_disclosure(monkeypatch):
    monkeypatch.setenv("RMI_REF_GMGN_CODE", "rugmunch")
    ref = gmgn_perps_referral("ETH")
    post = render_perps_card(_rows(), cta_symbol="ETH", referral=ref, commentary="Read.")
    assert post.count("<a href=") == 1
    assert "ETH.PERP_USDC?ref=rugmunch" in post
    assert DISCLOSURE in post
    assert "Risk" in post
    for sym in ("BTC", "ETH", "SOL"):
        assert f"<b>{sym}</b>" in post


def test_card_without_referral_states_reason_and_links_nothing(monkeypatch):
    monkeypatch.delenv("RMI_REF_GMGN", raising=False)
    monkeypatch.delenv("RMI_REF_GMGN_CODE", raising=False)
    ref = gmgn_perps_referral("SOL")
    post = render_perps_card(_rows(), cta_symbol="SOL", referral=ref, commentary="Read.")
    assert "<a href=" not in post
    assert "gmgn-perps:referral-not-configured" in post
    assert DISCLOSURE not in post


def test_card_never_invents_an_uncovered_market():
    ref = Referral("gmgn_perps", STANDARD, None, None, "gmgn-perps:unverified-market:DOGE")
    post = render_perps_card(_rows(), cta_symbol="DOGE", referral=ref, commentary="")
    assert "DOGE.PERP" not in post
    assert "<a href=" not in post


# ── lane build + fail-open ───────────────────────────────────────────────────


def _build(liq_data, fund_data):
    with (
        patch(
            "rugmunchbot.tools.cascade_predictor.cascade_watch",
            new=AsyncMock(return_value=_Result(liq_data)),
        ),
        patch(
            "rugmunchbot.tools.funding_dislocation.funding_dislocation",
            new=AsyncMock(return_value=_Result(fund_data)),
        ),
    ):
        return asyncio.run(build_perps_recap_post())


def test_build_posts_cta_for_nearest_liquidation_market(monkeypatch):
    monkeypatch.setenv("RMI_REF_GMGN_CODE", "rugmunch")
    watchlist = [
        {
            "asset": r["symbol"],
            "total_oi_usd": r["oi_usd"],
            "near_side": r["near_side"],
            "trigger_price_10x": r["trigger_price"],
            "distance_pct": r["distance_pct"],
        }
        for r in _rows()
    ]
    signals = [
        {"asset": "BTC", "richest_funding": {"rate": 0.0001, "venue": "hyperliquid"}},
        {"asset": "ETH", "richest_funding": {"rate": 0.0002, "venue": "binance"}},
    ]
    post = _build({"watchlist": watchlist}, {"signals": signals})
    assert post is not None
    assert post.perps["symbols"] == ["BTC", "ETH", "SOL"]
    assert post.perps["cta"]["symbol"] == "ETH"  # 1.4% is the nearest stack
    assert post.perps["cta"]["url"].endswith("/ETH.PERP_USDC?ref=rugmunch")
    assert post.count("<a href=") == 1


def test_build_returns_none_when_feed_is_empty():
    assert _build({}, {}) is None


def test_build_is_fail_open_on_exception():
    with patch(
        "rugmunchbot.tools.cascade_predictor.cascade_watch",
        new=AsyncMock(side_effect=RuntimeError("backend down")),
    ):
        assert asyncio.run(build_perps_recap_post()) is None
