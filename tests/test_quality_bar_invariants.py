"""Quality-bar invariant tests — the enterprise output contract, as code.

These tests LOCK the reporting standards established in the 2026-08-23
quality campaign. Any change that violates them fails CI:

  Q1. HONEST DATA — a value that was never reported never renders as a
      fabricated number (no "Holders 0" when holders are unknown, no "$0"
      when a value is missing). Unknown is "N/A"/"—", zero is zero.
  Q2. SIGNAL DENSITY — the scam matrix leads with flagged findings WITH
      evidence, collapses clean checks to a count, and never claims a check
      ran clean without data.
  Q3. VERDICT SPECIFICITY — when evidence exists the verdict names it;
      when nothing fired but checks passed, the verdict confirms what
      passed. Generic filler only as last resort.
  Q4. AUTHORITY GROUND TRUTH — on-chain mint/freeze authority facts
      override provider guesses, and renounce is reported as such.
  Q5. SMART MONEY HONESTY — per-wallet rows that cancel to zero keep
      net=0; aggregate buy_pressure never masks real row data.
"""

from __future__ import annotations

import pytest

from rugmunchbot.formatters import format_scan_report
from rugmunchbot.formatters._base._risk import _verdict_sentence
from rugmunchbot.formatters._scan_report_common._scam_matrix import (
    _format_scam_signals_section,
)
from rugmunchbot.formatters._wallet import format_wallet_report
from rugmunchbot.tools.scanner import _smart_money_token as smt


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"</?[a-z]+[^>]*>", "", text)


# ── Q1: honest data ──────────────────────────────────────────────────────


def test_q1_missing_holders_never_render_as_zero() -> None:
    r = {
        "name": "T",
        "symbol": "T",
        "chain": "solana",
        "address": "BKaXDgZxUSC9njpX89xpQ5USh2pnK1yzZvgk8Mg7pump",
        "risk_score": 10,
    }
    out = _strip_html(format_scan_report(r))
    assert "Holders" in out
    # The Key Metrics Holders line must NOT show a bare fabricated 0.
    for line in out.splitlines():
        if line.strip().startswith("Holders"):
            assert "0" not in line.replace("N/A", "").replace("100", ""), line


def test_q1_genuine_zero_holders_still_renders_zero() -> None:
    """A provider-reported 0 is REAL data and must stay visible."""
    r = {
        "name": "T",
        "symbol": "T",
        "chain": "solana",
        "address": "BKaXDgZxUSC9njpX89xpQ5USh2pnK1yzZvgk8Mg7pump",
        "holder_analysis": {"total_holders": 0},
        "risk_score": 10,
    }
    out = _strip_html(format_scan_report(r))
    assert any("Holders" in ln and "0" in ln for ln in out.splitlines())


def test_q1_wallet_age_zero_is_not_unknown() -> None:
    r = {
        "address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        "chain": "solana",
        "balance_usd": 12.5,
        "wallet_age_days": 0,
        "tx_count": None,
        "is_exchange": False,
        "is_contract": False,
        "is_fresh": True,
        "flags": [],
        "pnl_estimate": None,
        "top_tokens": [],
        "funding_source": "",
        "connected_wallets": [],
        "risk_score": 0,
    }
    out = _strip_html(format_wallet_report(r))
    assert "0 days" in out
    assert "Age: Unknown" not in out


def test_q1_wallet_holding_without_value_renders_dash_not_dollar_zero() -> None:
    r = {
        "address": "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
        "chain": "solana",
        "balance_usd": 12.5,
        "wallet_age_days": 30,
        "tx_count": 5,
        "is_exchange": False,
        "is_contract": False,
        "is_fresh": False,
        "flags": [],
        "pnl_estimate": None,
        "top_tokens": [{"symbol": "GHOST", "value_usd": None}],
        "funding_source": "",
        "connected_wallets": [],
        "risk_score": 0,
    }
    out = _strip_html(format_wallet_report(r))
    holdings_line = next(ln for ln in out.splitlines() if "GHOST" in ln)
    assert "—" in holdings_line
    assert "$0" not in holdings_line


# ── Q2: signal density ───────────────────────────────────────────────────


def test_q2_scam_matrix_flagged_first_with_evidence() -> None:
    rep = {"honeypot": True, "mintable": False}
    lines = _format_scam_signals_section(rep)
    body = lines[3:]
    first_flagged = next(i for i, ln in enumerate(body) if "\U0001f534" in ln)
    summary = next(i for i, ln in enumerate(body) if "checks passed clean" in ln)
    assert first_flagged < summary
    honeypot_row = body[first_flagged]
    assert "Honeypot" in honeypot_row
    assert "—" in honeypot_row or "blocked" in honeypot_row  # evidence attached


def test_q2_no_data_never_claimed_clean() -> None:
    lines = _format_scam_signals_section({"foo": "bar"})
    assert not any("checks passed clean" in ln for ln in lines[3:])
    assert any("No data:" in ln for ln in lines[3:])


def test_q2_matrix_stays_compact_under_mixed_data() -> None:
    rep = {
        "honeypot": False,
        "lp_locked": 0.9,
        "bundle_analysis": {"is_bundled": False},
        "mintable": False,
        "freezable": False,
        "buy_tax": 0.0,
        "sell_tax": 0.0,
    }
    lines = _format_scam_signals_section(rep)
    # 16 techniques must collapse to well under 16 rendered rows.
    assert len(lines) <= 3 + 6


# ── Q3: verdict specificity ──────────────────────────────────────────────


def test_q3_verdict_names_the_evidence() -> None:
    assert "mintable" in _verdict_sentence({"mintable": True}).lower()
    assert (
        "bundle"
        in _verdict_sentence({"bundle_analysis": {"is_bundled": True, "launch_buyers": 4}}).lower()
    )
    assert "not verified" in _verdict_sentence({"contract_verified": False}).lower()


def test_q3_clean_scan_confirms_what_passed() -> None:
    verdict = _verdict_sentence({"honeypot": False, "owner_renounced": True, "freezable": False})
    assert "Core checks passed" in verdict
    assert "renounced" in verdict.lower()


def test_q3_generic_filler_only_when_nothing_known() -> None:
    assert _verdict_sentence({}) == "No critical flags \u2014 normal caution advised."


# ── Q4: authority ground truth ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_q4_rpc_authority_overrides_provider_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    from rugmunchbot.tools.scanner import _solana_onchain as so

    async def facts(mint: str) -> dict[str, object]:
        return {
            "mint_authority": None,
            "freeze_authority": None,
            "decimals": 6,
            "supply_raw": 1000,
            "token_program": "spl",
        }

    async def none(*a: object, **k: object) -> None:
        return None

    monkeypatch.setattr(so, "resolve_mint_facts", facts)
    monkeypatch.setattr(so, "resolve_holder_stats", none)
    monkeypatch.setattr(so, "resolve_deployer", none)
    monkeypatch.setattr(so, "detect_same_slot_bundle", none)

    # Provider GUESSED mintable=True; on-chain says renounced — RPC wins.
    result = {"chain": "solana", "mintable": True}
    out = await so.enrich_scan_solana(
        result, "BKaXDgZxUSC9njpX89xpQ5USh2pnK1yzZvgk8Mg7pump", "solana"
    )
    assert out["mintable"] is False
    assert out["owner_renounced"] is True


# ── Q5: smart money honesty ──────────────────────────────────────────────


def test_q5_cancelled_rows_keep_zero_net() -> None:
    norm = smt._normalize(
        {
            "data": {
                "buy_pressure": 99.0,
                "activity": [{"wallet": "w1", "buy_7d": 5, "sell_7d": 5}],
            }
        },
        "addr",
        "solana",
    )
    assert norm is not None
    assert norm["net_bought"] == 0.0


def test_q5_aggregate_pressure_only_fills_when_no_rows() -> None:
    norm = smt._normalize({"data": {"smart_degen_count": 3, "buy_pressure": -12.5}}, "a", "sol")
    assert norm is not None
    assert norm["net_bought"] == -12.5
