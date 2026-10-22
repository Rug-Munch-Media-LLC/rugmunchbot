"""Rendering contract for the parity block on the scan card.

Rules under test: the cohort rollup always lists Team / Insiders / Snipers;
the KOL row appears only when a registry wallet actually matched; the impact
row appears only when a DexScreener pair backed it; a payload without the three
keys renders nothing at all (v1 cards stay byte-identical); an unavailable
section renders ONE em-dash line and its reason — never an invented zero.
"""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_parity import HEADING, format_parity_section

COHORTS = {
    "team": {"count": 2, "initial_buy_pct": 4.5, "current_pct": 4.5, "realized_sol": 0.0},
    "insiders": {"count": 0, "initial_buy_pct": None, "current_pct": None, "realized_sol": None},
    "snipers": {
        "count": 1,
        "bundled_n": 1,
        "initial_buy_pct": 2.1315,
        "current_pct": 0.0,
        "realized_sol": 0.749398,
    },
}
KOL = {
    "kol": [
        {"handle": "@a", "bought_sol": 1.5, "sold_sol": 0.25},
        {"handle": "@b", "bought_sol": 0.5, "sold_sol": 0.0},
        {"handle": "@c", "bought_sol": 0.1, "sold_sol": 0.0},
        {"handle": "@d", "bought_sol": 0.1, "sold_sol": 0.0},
    ]
}
IMPACT = {
    "pair_address": "PAIR",
    "quote_symbol": "SOL",
    "buy_1sol_pct": 0.121951,
    "sell_1sol_pct": 0.121802,
    "depth_2pct_usd": 1698.49,
}


def _render(payload: dict) -> str:
    return "\n".join(format_parity_section(payload))


def test_cohort_rollup_lists_all_three_with_pct_and_realised():
    out = _render({"cohorts": COHORTS})
    assert "Team: <b>2</b> | 4.5000% \u2192 4.5000% | realised 0.000000 SOL" in out
    assert "Snipers: <b>1 (1 bundled)</b> | 2.1315% \u2192 0.0000%" in out
    assert "realised 0.749398 SOL" in out


def test_missing_cohort_values_render_em_dash_not_zero():
    out = _render({"cohorts": COHORTS})
    assert "Insiders: <b>0</b> | \u2014 \u2192 \u2014 | realised \u2014" in out


def test_heading_present_once_when_any_section_renders():
    lines = format_parity_section({"cohorts": COHORTS})
    assert lines[0] == ""
    assert lines[1] == HEADING
    assert lines.count(HEADING) == 1


def test_kol_row_only_when_a_wallet_matched():
    assert "KOL" not in _render({"cohorts": COHORTS, "kol": {"kol": []}})
    assert "KOL" not in _render({"cohorts": COHORTS, "kol": {}})
    out = _render({"cohorts": COHORTS, "kol": KOL})
    assert "KOL: <b>4</b> matched \u2014 @a, @b, @c +1" in out
    assert "bought 2.200000 SOL / sold 0.250000 SOL" in out


def test_impact_row_only_when_a_pair_backed_it():
    assert "Impact" not in _render({"cohorts": COHORTS, "impact": {}})
    assert "Impact" not in _render({"cohorts": COHORTS, "impact": {"buy_1sol_pct": 1.0}})
    out = _render({"cohorts": COHORTS, "impact": IMPACT})
    assert "Impact: 1 SOL buy/sell <b>0.121951% / 0.121802%</b> | 2% depth $1,698" in out


def test_impact_without_quote_still_renders_dash_and_symbol():
    out = _render({"impact": {**IMPACT, "quote_symbol": None, "depth_2pct_usd": None}})
    assert "2% depth \u2014" in out
    assert "Impact" in out


def test_unavailable_section_renders_one_dash_line_and_its_reason():
    out = _render({"cohorts": {"reason": "scanner service returned 503"}, "kol": {}, "impact": {}})
    assert "Cohorts: \u2014 (scanner service returned 503)" in out
    assert "Team" not in out
    assert "Snipers" not in out


def test_v1_payload_without_parity_keys_renders_nothing():
    v1 = {"address": "So111", "score": 42, "risk_signals": ["mint authority"]}
    assert format_parity_section(v1) == []
    assert format_parity_section({"cohorts": {}, "kol": {}, "impact": {}}) == []


def test_long_report_drops_parity_block_before_overflow():
    from rugmunchbot.formatters._scan_report_parity import HEADING, TELEGRAM_LIMIT, fit_report

    body = "\n".join(["x" * 200] * 21)
    text = body + "\n\n" + HEADING + "\n  • Snipers: 1\n\nTAIL"
    assert len(text) > TELEGRAM_LIMIT
    out = fit_report(text)
    assert HEADING not in out
    assert "Snipers" not in out
    assert "TAIL" in out
    assert len(out) <= TELEGRAM_LIMIT
