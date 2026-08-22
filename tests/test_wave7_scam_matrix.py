"""Wave 7 — _scam_matrix: consolidated 16-row Scam Signals matrix.

Contracts probed against the implementation before asserting (oracle policy):
empty report renders NO section; any non-empty report always lists all 16
techniques; missing data renders "—" (never a fabricated clean); field
extraction tolerates flat / nested-evidence / risk_signals shapes.
"""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_common._scam_matrix import (
    _SCAM_SIGNAL_GREEN,
    _SCAM_SIGNAL_RED,
    _SCAM_SIGNAL_UNKNOWN,
    _format_scam_signals_section,
)

ROW_COUNT = 16  # header(3 lines: blank, title, sep) + 16 technique rows


def _states(lines: list[str]) -> list[str]:
    # Row lines look like "  {emoji} {label}"; strip() then take token 0.
    return [ln.strip().split()[0] for ln in lines[3:]]


def test_empty_report_renders_no_section() -> None:
    assert _format_scam_signals_section({}) == []


def test_non_dict_report_renders_no_section() -> None:
    assert _format_scam_signals_section("nope") == []  # type: ignore[arg-type]


def test_any_report_lists_all_16_techniques_honestly() -> None:
    lines = _format_scam_signals_section({"foo": "bar"})
    assert len(lines) == 3 + ROW_COUNT
    assert "🔍 <b>Scam Signals</b>" in lines[1]
    assert set(_states(lines)) == {_SCAM_SIGNAL_UNKNOWN}


def test_honeypot_red_via_flat_detected_string() -> None:
    lines = _format_scam_signals_section({"honeypot": "detected"})
    hp = lines[3]
    assert hp.startswith(f"  {_SCAM_SIGNAL_RED} Honeypot (sell-block)")
    assert "sells may be blocked" in hp


def test_honeypot_green_via_nested_evidence_security() -> None:
    rep = {"evidence": {"security": {"is_honeypot": False}}}
    lines = _format_scam_signals_section(rep)
    assert lines[3].startswith(f"  {_SCAM_SIGNAL_GREEN} Honeypot (sell-block)")
    assert "sellable" in lines[3]


def test_threat_flags_normalized_lowercase_trigger_red() -> None:
    # Mixed-case + noise in the threats list still matches the honeypot flag.
    lines = _format_scam_signals_section({"threats": ["HONEYpot", "", "anti-whale"]})
    assert lines[3].startswith(f"  {_SCAM_SIGNAL_RED} Honeypot (sell-block)")


def test_mint_authority_states_by_shape() -> None:
    red = _format_scam_signals_section({"mint_authority": "GkP…mint"})
    assert f"{_SCAM_SIGNAL_RED} Mint authority" in red[3 + 7]
    green = _format_scam_signals_section({"mintable": False})
    assert f"{_SCAM_SIGNAL_GREEN} Mint authority" in green[3 + 7]
    unknown = _format_scam_signals_section({"mint_authority": "none"})
    assert f"{_SCAM_SIGNAL_UNKNOWN} Mint authority" in unknown[3 + 7]


def test_lp_lock_red_below_50_green_above() -> None:
    lv = {"sentinel": {"free": {"liquidity_verification": {"total_locked_percentage": 12.4}}}}
    red = _format_scam_signals_section(lv)
    assert f"{_SCAM_SIGNAL_RED} Rug / LP removal" in red[3 + 1]
    assert "LP only 12% locked" in red[3 + 1]

    lv_ok = {"sentinel": {"free": {"liquidity_verification": {"total_locked_percentage": 95.0}}}}
    green = _format_scam_signals_section(lv_ok)
    assert f"{_SCAM_SIGNAL_GREEN} Rug / LP removal" in green[3 + 1]
    assert "LP 95% locked" in green[3 + 1]


def test_bundle_red_with_wallet_count_detail() -> None:
    rep = {"bundle_analysis": {"is_bundled": True, "launch_buyers": 3}}
    lines = _format_scam_signals_section(rep)
    row = lines[3 + 2]
    assert row.startswith(f"  {_SCAM_SIGNAL_RED} Bundle / sniper")
    assert "3 wallets" in row


def test_details_are_numeric_or_static_only() -> None:
    # Every detail is f-string-numeric or static ("sellable", "coordinated
    # launch", …); html.escape(detail) in the renderer is defense-in-depth.
    # There is deliberately NO report-controlled free-text detail to inject.
    rep = {"bundle_analysis": {"is_bundled": True, "launch_buyers": "not-a-number"}}
    lines = _format_scam_signals_section(rep)
    assert "coordinated launch" in lines[3 + 2]
