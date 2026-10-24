"""Per-chain trending cards — icons, score reason, paid-placement disclosure.

Contracts: rank/price/change/volume/liquidity/score render per card, icons
fall back safely when absent, and paid placements never take a rank.
"""

from __future__ import annotations

from rugmunchbot.handlers._market_helpers import (
    BOOST_DISCLOSURE,
    _build_trending_kb,
    _icon_slot,
    _is_trending_chain,
    _merge_trending_sources,
    _render_boosted_lines,
    _render_token_card,
    _render_trending_text,
    _score_line,
    _split_paid,
    _token_icon,
)


def _token(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "address": "0xAAAABBBBCCCCDDDD",
        "name": "Pepe",
        "symbol": "PEPE",
        "chain": "solana",
        "price_usd": 0.00123,
        "change_24h": 12.5,
        "volume_24h": 1_200_000,
        "liquidity_usd": 50_000,
        "created_at": 1_699_999_999_000,
    }
    base.update(over)
    return base


# ── icons ────────────────────────────────────────────────────────────────────


def test_icon_slot_links_a_real_logo_url() -> None:
    token = {"info": {"imageUrl": "https://cdn.example/logo.png"}}
    assert _token_icon(token) == "https://cdn.example/logo.png"
    slot = _icon_slot(token)
    assert slot.startswith('<a href="https://cdn.example/logo.png">')
    assert "🪙" in slot


def test_icon_slot_falls_back_without_a_usable_url() -> None:
    for token in ({}, {"info": {"imageUrl": ""}}, {"icon": "javascript:alert(1)"}, {"icon": 7}):
        assert _token_icon(token) is None
        assert _icon_slot(token) == "🪙"  # plain glyph, never a broken image


# ── score reason ────────────────────────────────────────────────────────────


def test_score_line_reports_score_and_drivers() -> None:
    line = _score_line(_token())
    assert "Score <b>" in line
    assert "driver:" in line
    for component in ("vol", "liq", "grw", "mom", "age"):
        assert f"{component} " in line


def test_score_line_notes_the_risk_penalty() -> None:
    line = _score_line(_token(risk_score=90))
    assert "risk \u2212" in line


# ── paid placement is not ranked ────────────────────────────────────────────


def test_boost_leg_is_tagged_paid_and_split_out() -> None:
    rug = [{"address": "0xORGANIC", "name": "Org", "symbol": "ORG", "volume_24h": 10}]
    boosts = [{"address": "0xPAID", "name": "Paid", "symbol": "PAY", "volume_24h": 10}]
    merged = _merge_trending_sources(rug, boosts, None, limit=10)
    organic, paid = _split_paid(merged)
    assert [t["address"] for t in paid] == ["0xPAID"]
    assert [t["address"] for t in organic] == ["0xORGANIC"]


def test_boosted_token_also_trending_organically_stays_organic() -> None:
    rug = [{"address": "0xBOTH", "name": "Both", "symbol": "BOTH", "volume_24h": 10}]
    boosts = [{"address": "0xboth", "name": "Both", "symbol": "BOTH", "volume_24h": 10}]
    merged = _merge_trending_sources(rug, boosts, None, limit=10)
    _, paid = _split_paid(merged)
    assert paid == []


# ── card + list rendering ───────────────────────────────────────────────────


def test_card_shows_rank_symbol_price_change_volume_liquidity() -> None:
    lines = _render_token_card(3, _token(), "solana", "24h")
    card = "\n".join(lines)
    assert card.startswith("3. ")
    assert "<b>Pepe</b> (PEPE)" in card
    assert "Price: $0.00123" in card
    assert "12.50%" in card
    assert "Vol: $1.20M" in card
    assert "Liq: $50.0K" in card
    assert "Score <b>" in card  # score reason
    assert "<code>0xAAAABBBBCCCCDDDD</code>" in card


def test_list_separates_paid_block_with_disclosure() -> None:
    tokens = [_token(), _token(address="0xPAID", name="Paid", symbol="PAY", paid_placement=True)]
    text = _render_trending_text(tokens, "solana", "24h")
    assert "1. " in text
    assert "2. " not in text  # the paid token takes no rank
    assert "🚀 <b>Boosted</b>" in text
    assert f"<i>{BOOST_DISCLOSURE}</i>" in text
    assert "Paid placement" in text
    assert text.index("Boosted") < text.index("Chains:")  # disclosure before footer


def test_list_omits_boosted_block_when_nothing_is_paid() -> None:
    text = _render_trending_text([_token()], "solana", "24h")
    assert "Boosted" not in text
    assert "Paid placement" not in text
    assert _render_boosted_lines([], "solana", "24h") == []


def test_unknown_chain_arg_is_named_not_silently_empty() -> None:
    assert _is_trending_chain("solana") is True
    assert _is_trending_chain("foobar") is False
    text = _render_trending_text([], "foobar", "24h")
    assert "Unknown chain" in text


# ── per-chain keyboard ──────────────────────────────────────────────────────


def test_chain_keyboard_covers_every_renderable_chain() -> None:
    kb = _build_trending_kb([], "arbitrum", "24h")
    chain_row = kb.inline_keyboard[-3]
    callbacks = [b.callback_data for b in chain_row]
    assert callbacks == [
        "trending_chain_solana",
        "trending_chain_ethereum",
        "trending_chain_bsc",
        "trending_chain_base",
        "trending_chain_arbitrum",
        "trending_chain_all",
    ]
    assert [b.text for b in chain_row] == ["SOL", "ETH", "BSC", "BASE", "[ARB]", "ALL"]
