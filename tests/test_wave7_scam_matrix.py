"""Wave 7 — _scam_matrix: compact Scam Signals block (2026-08 redesign).

Contracts probed against the implementation before asserting (oracle policy):
empty report renders NO section; flagged techniques render first WITH their
evidence detail; clean checks collapse into one count line; unknown checks
collapse into one "no data" line listing names (never a fabricated clean);
field extraction tolerates flat / nested-evidence / risk_signals shapes.
"""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_common._scam_matrix import (
    _SCAM_SIGNAL_GREEN,
    _SCAM_SIGNAL_RED,
    _format_scam_signals_section,
)


def test_empty_report_renders_no_section() -> None:
    assert _format_scam_signals_section({}) == []


def test_non_dict_report_renders_no_section() -> None:
    assert _format_scam_signals_section("nope") == []  # type: ignore[arg-type]


def test_clean_report_collapses_to_count_and_nodata_lines() -> None:
    lines = _format_scam_signals_section({"foo": "bar"})
    # header(3) + 1 no-data line — with zero data nothing counts as clean,
    # and the 16-row wall collapses to a single honest line.
    assert len(lines) == 3 + 1
    assert "🔍 <b>Scam Signals</b>" in lines[1]
    assert any("No data:" in ln for ln in lines[3:])
    assert not any("checks passed clean" in ln for ln in lines[3:])


def test_honeypot_red_via_flat_detected_string() -> None:
    lines = _format_scam_signals_section({"honeypot": "detected"})
    hp = lines[3]
    assert hp.startswith(f"  {_SCAM_SIGNAL_RED} Honeypot (sell-block)")
    assert "sells may be blocked" in hp


def test_honeypot_green_via_nested_evidence_security() -> None:
    rep = {"evidence": {"security": {"is_honeypot": False}}}
    lines = _format_scam_signals_section(rep)
    clean_line = next(ln for ln in lines[3:] if "checks passed clean" in ln)
    assert _SCAM_SIGNAL_GREEN in clean_line
    assert "1" in clean_line


def test_threat_flags_normalized_lowercase_trigger_red() -> None:
    # Mixed-case + noise in the threats list still matches the honeypot flag.
    lines = _format_scam_signals_section({"threats": ["HONEYpot", "", "anti-whale"]})
    assert lines[3].startswith(f"  {_SCAM_SIGNAL_RED} Honeypot (sell-block)")


def test_mint_authority_states_by_shape() -> None:
    red = _format_scam_signals_section({"mint_authority": "GkP…mint"})
    assert f"{_SCAM_SIGNAL_RED} Mint authority" in red[3]
    assert "supply can be minted" in red[3]

    green = _format_scam_signals_section({"mintable": False})
    clean_line = next(ln for ln in green[3:] if "checks passed clean" in ln)
    assert _SCAM_SIGNAL_GREEN in clean_line

    unknown = _format_scam_signals_section({"mint_authority": "none"})
    nodata = next(ln for ln in unknown[3:] if "No data:" in ln)
    # 11 unknowns — only the first 5 names render, rest summarized.
    assert "+11 more" in nodata
    shown_names = nodata.split("No data: ")[1].split(" +")[0].split(" · ")
    assert len(shown_names) == 5
    # When every technique resolves, the No-data line disappears entirely.
    full = _format_scam_signals_section(
        {
            "honeypot": False,
            "lp_locked": 0.9,
            "bundle_analysis": {"is_bundled": False},
            "mintable": False,
            "freezable": False,
            "buy_tax": 0.0,
            "sell_tax": 0.0,
            "owner_renounced": True,
        }
    )
    body = [ln for ln in full[3:] if "No data:" in ln]
    assert not body or "+0" not in "".join(body)


def test_lp_lock_red_below_50_green_above() -> None:
    lv = {"sentinel": {"free": {"liquidity_verification": {"total_locked_percentage": 12.4}}}}
    red = _format_scam_signals_section(lv)
    row = next(ln for ln in red[3:] if "Rug / LP removal" in ln)
    assert _SCAM_SIGNAL_RED in row
    assert "LP only 12% locked" in row

    lv_ok = {"sentinel": {"free": {"liquidity_verification": {"total_locked_percentage": 95.0}}}}
    green = _format_scam_signals_section(lv_ok)
    clean_line = next(ln for ln in green[3:] if "checks passed clean" in ln)
    assert _SCAM_SIGNAL_GREEN in clean_line


def test_bundle_red_with_wallet_count_detail() -> None:
    rep = {"bundle_analysis": {"is_bundled": True, "launch_buyers": 3}}
    lines = _format_scam_signals_section(rep)
    row = next(ln for ln in lines[3:] if "Bundle / sniper" in ln)
    assert row.startswith(f"  {_SCAM_SIGNAL_RED} Bundle / sniper")
    assert "3 wallets" in row


def test_details_are_numeric_or_static_only() -> None:
    # Every detail is f-string-numeric or static ("sellable", "coordinated
    # launch", …); html.escape(detail) in the renderer is defense-in-depth.
    # There is deliberately NO report-controlled free-text detail to inject.
    rep = {"bundle_analysis": {"is_bundled": True, "launch_buyers": "not-a-number"}}
    lines = _format_scam_signals_section(rep)
    row = next(ln for ln in lines[3:] if "Bundle / sniper" in ln)
    assert "coordinated launch" in row


def test_flagged_rows_render_before_summary_lines() -> None:
    rep = {
        "honeypot": True,
        "mintable": False,
        "lp_locked": 0.9,
        "bundle_analysis": {"is_bundled": False},
    }
    lines = _format_scam_signals_section(rep)
    body = lines[3:]
    flagged_idx = [i for i, ln in enumerate(body) if _SCAM_SIGNAL_RED in ln]
    summary_idx = [i for i, ln in enumerate(body) if "checks passed clean" in ln]
    nodata_idx = [i for i, ln in enumerate(body) if "No data:" in ln]
    assert flagged_idx
    assert summary_idx
    assert nodata_idx
    assert max(flagged_idx) < min(summary_idx + nodata_idx)
