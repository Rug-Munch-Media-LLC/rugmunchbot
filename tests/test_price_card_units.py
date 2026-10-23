"""Unit tests for the multi-chain /price card renderer (pure, no network).

Contract: every field comes from the caller's snapshot. A field the source did
not report renders as "-" WITH the reason — a zero is never invented.
"""

from __future__ import annotations

from rugmunchbot.handlers._price_card import MISSING, render_price_card
from rugmunchbot.services.pricing import PriceResult

EVM = "0x6982508145454ce325ddbe47a25d4ec3d2311933"
SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOURCE = "dexscreener:/tokens/{address}"

FULL = PriceResult(
    price_usd=0.0000123,
    change_24h=-4.5,
    liquidity=450_000.0,
    source=SOURCE,
    volume_24h=99_000.0,
    market_cap=1_200_000.0,
    chain="ethereum",
)


def test_full_card_renders_every_field() -> None:
    text = render_price_card(
        address=EVM, chain="ethereum", data=FULL, fetched_at="2026-10-24T09:05:00+00:00"
    )
    assert "Token Price" in text
    assert "$0.0000123000" in text  # price below 0.01 keeps full precision
    assert "Market cap: $1.20M" in text
    assert "Liquidity: $450.0K" in text
    assert "24h change: -4.50%" in text
    assert "Volume 24h: $99.0K" in text
    assert SOURCE in text
    assert "2026-10-24 09:05 UTC" in text
    assert MISSING not in text


def test_chart_link_and_market_link_are_present() -> None:
    text = render_price_card(address=EVM, chain="ethereum", data=FULL)
    assert 'href="https://dexscreener.com/ethereum/' + EVM + '"' in text
    assert "/markets/token/" + EVM in text


def test_dict_payload_matches_dataclass_payload() -> None:
    payload = FULL.to_dict() | {
        "market_cap": FULL.market_cap,
        "fetched_at": "2026-10-24T09:05:00+00:00",
    }
    from_dc = render_price_card(
        address=EVM, chain="ethereum", data=FULL, fetched_at="2026-10-24T09:05:00+00:00"
    )
    from_dict = render_price_card(
        address=EVM, chain="ethereum", data=payload, fetched_at=payload["fetched_at"]
    )
    assert from_dc == from_dict


def test_unknown_fields_render_missing_with_reason_not_zero() -> None:
    thin = PriceResult(price_usd=0.5, source="backend:/api/v1/databus/token")
    text = render_price_card(address=EVM, chain="ethereum", data=thin)
    assert "market cap not reported by backend:/api/v1/databus/token" in text
    assert "liquidity not reported by" in text
    assert "24h change not reported by" in text
    assert "volume not reported by" in text
    assert "$0.0000000000" not in text


def test_zero_is_treated_as_unreported() -> None:
    # A zero mcap/liquidity is "no data" from the source, not "$0".
    zeroed = PriceResult(
        price_usd=0.5, source="scanner:/api/v2/scanner/token/price", market_cap=0.0, liquidity=0.0
    )
    text = render_price_card(address=EVM, chain="ethereum", data=zeroed)
    assert "$0.0000000000" not in text
    assert "market cap not reported" in text


def test_chain_is_autodetected_when_not_given() -> None:
    text = render_price_card(address=SOL, data=FULL)
    assert "Solana" in text
    assert 'href="https://dexscreener.com/solana/' + SOL + '"' in text


def test_missing_timestamp_is_stated_not_guessed() -> None:
    text = render_price_card(address=EVM, chain="ethereum", data=FULL)
    assert "not stamped by the caller" in text
