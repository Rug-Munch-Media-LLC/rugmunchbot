"""Rendering tests for the scan card's forensic block (fresh buyers + CEX).

Three states per block, because the card is rendered from a payload an older
backend may not fill yet:

  * all fields     -> the full line/rows/signals, in reading order;
  * partial fields -> only the fields that exist (never a fabricated 0%);
  * none           -> byte-identical legacy card, and the ``First buyers``
                      view returns "" so the callback can say "no data".

Run: python -m pytest tests/test_vcard_forensics.py -v
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._vcard import format_verdict_card
from rugmunchbot.formatters._vcard_buyers_view import full_buyers_view
from rugmunchbot.formatters._vcard_freshbuyers import first_buyer_lines
from rugmunchbot.formatters._vcard_funding import funding_lines

CORE: dict[str, Any] = {
    "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "name": "Scammy",
    "symbol": "SCAM",
    "chain": "solana",
    "risk_score": 82,
    "honeypot": False,
    "buy_tax": 0.0,
    "sell_tax": 0.05,
}

BUYERS = [
    {
        "address": "0xab12cd34ef5678901234567890abcdef12345678",
        "first_buy_ts": "2026-10-23T01:00:00Z",
        "bought_sol": 4.25,
        "current_pct": 4.1,
        "sold_pct": 0.0,
        "realized_sol": 0.0,
        "wallet_age_days": 0.2,
        "funder_type": "deployer",
        "cluster_id": "c1",
    },
    {
        "address": "0xdeadbeef00112233445566778899aabbccddeeff",
        "first_buy_ts": "2026-10-23T01:05:00Z",
        "bought_sol": 1.1,
        "current_pct": 1.2,
        "sold_pct": 40.0,
        "wallet_age_days": 0.6,
        "funder_type": "cex",
    },
    {
        "address": "0x9999888877776666555544443333222211110000",
        "first_buy_ts": "2026-10-20T01:05:00Z",
        "bought_sol": 9.0,
        "current_pct": 8.5,
        "wallet_age_days": 90.0,
        "funder_type": "wallet",
    },
]


def scam_payload() -> dict[str, Any]:
    """Every forensic field present — the render the alerting degen sees."""
    r = dict(CORE)
    r.update(
        first_buyers=BUYERS,
        fresh_wallets={
            "count": 12,
            "share_of_first_buyers": 0.6,
            "under_1d": 8,
            "under_7d": 12,
            "funded_by_deployer": 6,
            "funded_by_same_funder": 8,
            "p50_age_days": 0.3,
        },
        scam_signals=[
            {"id": "x", "severity": "medium", "text": "thin liquidity", "metric": 1200},
            {
                "id": "shared_funder",
                "severity": "critical",
                "text": "8 first buyers share funder 0xab\u2026cd",
                "metric": 8,
            },
        ],
        cex_funded_fresh={
            "count": 9,
            "pct_of_holders": 0.18,
            "pct_of_supply": 0.062,
            "pct_of_first_buyers": 0.25,
            "median_age_hours": 36,
            "window_h": 24,
            "top_funders": [{"name": "Binance", "count": 4}, {"name": "ChangeNOW", "count": 2}],
        },
        funding_mix={"cex": 0.4, "deployer": 0.2, "bridge": 0.1, "unknown": 0.3},
        recent_funding={"wallets_n": 14, "window_h": 24, "funded_by_cex": 7},
        holder_quality={
            "exchange_pct": 0.12,
            "fresh_pct": 0.38,
            "insider_pct": 0.09,
            "bundle_pct": 0.06,
            "kol_pct": 0.04,
            "dev_pct": 0.02,
        },
    )
    return r


def clean_payload() -> dict[str, Any]:
    """A clean token: established buyers, no CEX-funded cohort, no signals."""
    r = dict(CORE, risk_score=12, symbol="CLEAN", name="Clean")
    r["first_buyers"] = [BUYERS[2]]
    r["fresh_wallets"] = {"count": 0, "under_7d": 0, "p50_age_days": 41.0}
    r["holder_quality"] = {"fresh_pct": 0.02, "whale_pct": 0.11}
    return r


def test_full_block_order_and_content() -> None:
    lines = first_buyer_lines(scam_payload())
    assert lines[0] == ""
    assert lines[1] == (
        "\U0001f9ea Fresh buyers: 12/20 under 7d \u00b7 6 funded by deployer "
        "\u00b7 8 share funder \u00b7 p50 age 0.3d"
    )
    assert "0xab\u2026" in lines[2]
    assert "0.2d" in lines[2]
    assert "deployer" in lines[2]
    assert "holds 4.1%" in lines[2]
    assert lines[-1].startswith("  Signals: \U0001f6a8")  # critical first
    assert "8 first buyers share funder" in lines[-1]
    assert len(lines) - 1 <= 5  # content cap


def test_cex_funding_block() -> None:
    lines = funding_lines(scam_payload())
    assert lines[1].startswith("\U0001f3e6\u27a1\ufe0f\U0001f464 18% of holders")
    assert "9 wallets" in lines[1]
    assert "6% supply" in lines[1]
    assert "25% of first buyers" in lines[1]
    assert "median 36h" in lines[1]
    assert lines[2].startswith("  \U0001f4b8 Top funders: Binance (4 wallets)")
    assert lines[3] == (
        "\U0001f465 Holders: fresh 38% \u00b7 insiders 9% \u00b7 bundle 6% "
        "\u00b7 KOL 4% \u00b7 dev 2%"
    )
    assert "\U0001f4b8 14 wallets funded in 24h (7 via CEX)" in lines[4]
    assert len(lines) - 1 <= 4


def test_partial_supply_renders_partial_not_a_percentage() -> None:
    r = {"cex_funded_fresh": {"count": 3, "pct_of_holders": 0.05}}
    lines = funding_lines(r)
    assert "supply \u2014 (partial holder set)" in lines[1]
    assert "% supply" not in lines[1]


def test_absent_payload_renders_nothing() -> None:
    r = dict(CORE)
    assert first_buyer_lines(r) == []
    assert funding_lines(r) == []
    card = format_verdict_card(r)
    assert "Fresh buyers" not in card
    assert "Funded by CEX" not in card
    assert "Holders:" not in card
    assert full_buyers_view(r) == ""


def test_legacy_dict_shape_is_accepted() -> None:
    r = dict(CORE, first_buyers={"buyers": BUYERS, "limit": 25})
    lines = first_buyer_lines(r)
    assert "0xab\u2026" in lines[1]


def test_clean_token_has_no_suspects_and_no_signals() -> None:
    lines = first_buyer_lines(clean_payload())
    assert lines[1].startswith("\U0001f9ea Fresh buyers: 0/1 under 7d")
    assert not any("Signals:" in line for line in lines)
    assert not any("deployer" in line for line in lines)
    assert not any("\U0001f3e6" in line for line in funding_lines(clean_payload()))


def test_card_inserts_block_after_safety_row() -> None:
    card = format_verdict_card(scam_payload())
    lines = card.split("\n")
    safety = next(i for i, line in enumerate(lines) if line.startswith("Safety"))
    assert lines[safety + 1] == ""
    assert lines[safety + 2].startswith("\U0001f9ea Fresh buyers:")


def test_card_drops_forensics_first_when_over_budget() -> None:
    r = scam_payload()
    r["top_holders"] = [{"address": "0xfeed", "pct": 9.9}] * 3
    full = format_verdict_card(r)
    assert "\U0001f3e6\u27a1\ufe0f\U0001f464" in full
    assert "\U0001f9ea Fresh buyers:" in full
    # One char tighter: the CEX / holder group dies, core rows survive.
    tight = format_verdict_card(r, max_chars=len(full) - 1)
    assert len(tight) <= len(full) - 1
    assert "\U0001f3e6\u27a1\ufe0f\U0001f464" not in tight
    assert "\U0001f9ea Fresh buyers:" in tight
    assert "Safety" in tight
    assert "Market" in tight
    # One char tighter than that: the first-buyer block dies too, before any
    # legacy optional block (holders/deployer/links) is touched.
    greedy = format_verdict_card(r, max_chars=len(tight) - 1)
    assert "\U0001f9ea Fresh buyers:" not in greedy
    assert "0xfeed" in greedy  # top-holder block still present
    assert "Safety" in greedy
    assert "Market" in greedy


def test_full_list_view_top10() -> None:
    r = scam_payload()
    r["first_buyers"] = [
        dict(BUYERS[0], address=f"0x{i:040x}", first_buy_ts=f"2026-10-23T01:{i:02d}:00Z")
        for i in range(15)
    ]
    view = full_buyers_view(r)
    assert "top 10" in view
    assert "earliest 10 of 15 tracked" in view
    assert view.count("<code>") == 10
    assert len(view) <= 4096
    assert "Fresh buyers:" in view


def test_full_list_view_fits_small_budget() -> None:
    r = scam_payload()
    view = full_buyers_view(r, max_chars=420)
    assert len(view) <= 420
    assert "First buyers" in view


def test_no_fabricated_zero_percent() -> None:
    r = {"holder_quality": {"fresh_pct": None, "kol_pct": 0}}
    line = funding_lines(r)[1]
    assert "fresh" not in line
    assert "KOL 0%" in line  # a real, reported zero is printed
