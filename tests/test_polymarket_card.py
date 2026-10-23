"""Card rendering: sourced numbers, honest wallet line, disclosures only when real."""

from __future__ import annotations

from datetime import UTC, datetime

from rugmunchbot.polymarket.card import MAX_CARD_CHARS, render_content_card
from rugmunchbot.polymarket.models import FlowEvidence, FlowTrade, SelectedMarket, WalletState

_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
_WALLET = WalletState(
    address="0x1E3AC01d0fdb976179790BDD02823196A92705C9", value_usd=0.0, position_count=0
)
_MARKET = SelectedMarket(
    condition_id="0xaaa111",
    question="Will Bitcoin close above $150k on December 31?",
    slug="btc-150k-dec31",
    category="crypto",
    yes_price=0.62,
    volume_usd=42_000_000,
    volume_24h_usd=3_200_000,
    liquidity_usd=850_000,
    change_1d=0.09,
    end_date="2026-10-26T00:00:00Z",
    hours_to_resolution=36.0,
    url="https://polymarket.com/event/btc-150k-dec31",
)
_FLOW = FlowEvidence(
    condition_id="0xaaa111",
    trades=(
        FlowTrade(
            wallet="0x92839e2d4f467ae41dc141913585afad1d474277",
            side="BUY",
            outcome="Yes",
            shares=25_000.0,
            price=0.62,
            notional_usd=15_500.0,
            timestamp=1792778286,
            tx_hash="0xhash1",
        ),
    ),
    low_history_wallets=("0x6100acd1f9ebe426750cb5cf7bc89e262beaed95",),
    same_outcome_wallets=("0x92839e2d", "0x6100acd1", "0x4283b8ba"),
    total_notional_usd=15_500.0,
    window_note="Observed in public trades from 2026-10-23 12:00 UTC to 2026-10-23 12:00 UTC.",
)


def test_card_carries_market_odds_volume_source_and_timestamp() -> None:
    text = render_content_card(_MARKET, _FLOW, _WALLET, generated_at=_NOW)
    assert "Will Bitcoin close above $150k" in text
    assert "YES 62.0% (+9.0 pts/24h)" in text
    assert "$3.20M" in text
    assert "https://polymarket.com/event/btc-150k-dec31" in text
    assert "Generated 2026-10-24 12:00 UTC" in text
    assert "$15,500" in text
    assert "0x9283…4277" in text  # truncated, full address not published


def test_empty_wallet_is_reported_as_zero() -> None:
    text = render_content_card(_MARKET, _FLOW, _WALLET, generated_at=_NOW)
    assert "holds $0.00 and has no open positions" in text


def test_unknown_wallet_value_is_not_zero() -> None:
    unknown = WalletState(address="0xabc", value_usd=0.0, position_count=0, value_known=False)
    text = render_content_card(_MARKET, _FLOW, unknown, generated_at=_NOW)
    assert "value unavailable" in text
    assert "$0.00" not in text


def test_no_referral_or_builder_line_unless_configured() -> None:
    text = render_content_card(_MARKET, _FLOW, _WALLET, generated_at=_NOW)
    assert "Referral:" not in text
    assert "Builder code:" not in text


def test_disclosures_appear_when_operator_supplies_them() -> None:
    text = render_content_card(
        _MARKET,
        _FLOW,
        _WALLET,
        generated_at=_NOW,
        referral_url="https://polymarket.com/?r=example",
        builder_code="0x" + "ab" * 32,
    )
    assert "Referral: https://polymarket.com/?r=example" in text
    assert "Builder code: 0x" + "ab" * 32 in text
    assert "100 bps taker / 50 bps maker" in text


def test_card_is_disclaimed_and_never_accuses() -> None:
    text = render_content_card(_MARKET, _FLOW, _WALLET, generated_at=_NOW)
    lowered = text.lower()
    assert "not financial advice" in lowered
    assert "public on-chain" in lowered
    for banned in ("insider", "insider-style", "manipulat", "scam", "guilty", "fraud"):
        assert banned not in lowered
    assert len(text) <= MAX_CARD_CHARS
