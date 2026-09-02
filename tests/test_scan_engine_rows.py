"""Tests for the new scanner-engine rows in the /scan report (2026-08-26).

Covers: dev_sold, dex_paid, price_metrics (ATH), sniper_count,
holder_concentration (gini/hhi), wallet_metrics (first-tx %) and
contract_authority — rendered in the Key Metrics table, skipped
gracefully when payloads are None/unavailable.
"""

from __future__ import annotations

from rugmunchbot.formatters._scan_report_main import format_scan_report


def _report() -> dict[str, object]:
    return {
        "name": "Pepe",
        "symbol": "PEPE",
        "chain": "solana",
        "address": "0x" + "ab" * 20,
        "risk_score": 55,
    }


def test_new_engine_rows_render() -> None:
    r = _report()
    r.update(
        dev_sold={"result": "scanned", "dev_sold": True, "sell_count": 3, "total_sold_pct": 12.5},
        dex_paid={
            "result": "scanned",
            "dex_paid": True,
            "dex_count": 2,
            "dexes": ["uniswap_v3", "curve"],
        },
        price_metrics={
            "result": "scanned",
            "ath": 0.0013,
            "current_price": 0.0012,
            "change_1h_pct": 110.0,
        },
        sniper_count={
            "result": "scanned",
            "sniper_count": 14,
            "sniper_pct": 5.9,
            "first_buy_block": 12345,
        },
        holder_concentration={
            "top10_pct": 20.0,
            "gini": 0.65,
            "hhi": 1800,
            "class": "concentrated",
            "total_holders": 950,
        },
        wallet_metrics={
            "result": "scanned",
            "holders_analyzed": 200,
            "fresh_wallet_pct": 11.0,
            "cex_funded_pct": 14.0,
            "first_tx_pct": 33.0,
        },
        contract_authority={
            "solana_authorities": {
                "mint_authority": {"is_renounced": False},
                "freeze_authority": {"is_renounced": True},
            }
        },
        bundle_analysis={"result": "scanned", "is_bundled": False, "confidence": 10.0},
    )
    out = format_scan_report(r)
    assert "Dev Sold" in out
    assert "🔴" in out
    assert "3 sells" in out
    assert "12.5% of supply" in out
    assert "DEX Paid" in out
    assert "uniswap_v3" in out
    assert "ATH" in out
    assert "$0.0013" in out
    assert "1h +110.0%" in out
    assert "Snipers" in out
    assert "<b>14</b>" in out
    assert "(5.9%)" in out
    assert "Gini Coeff" in out
    assert "0.65" in out
    assert "HHI" in out
    assert "1,800" in out
    assert "First Tx" in out
    assert "33.0%" in out
    assert "Mint Auth" in out
    assert "🔴 <b>ACTIVE</b>" in out
    assert "Freeze Auth" in out
    assert "🟢 <b>renounced</b>" in out


def test_new_engine_rows_skipped_when_none() -> None:
    out = format_scan_report(_report())
    assert "Dev Sold" not in out
    assert "DEX Paid" not in out
    assert "ATH" not in out
    assert "Snipers" not in out
    assert "Gini Coeff" not in out
    assert "First Tx" not in out


def test_unavailable_engine_payloads_render_nothing() -> None:
    r = _report()
    r.update(
        dev_sold={"result": "unavailable"},
        dex_paid={"result": "unavailable"},
        price_metrics={"result": "unavailable"},
        sniper_count={"result": "unavailable"},
        wallet_metrics={"result": "unavailable"},
    )
    out = format_scan_report(r)
    assert "Dev Sold" not in out
    assert "DEX Paid" not in out
    assert "ATH" not in out
    assert "Snipers" not in out


def test_dev_sold_clean_renders_green() -> None:
    r = _report()
    r["dev_sold"] = {"result": "scanned", "dev_sold": False}
    out = format_scan_report(r)
    assert "Dev Sold" in out
    assert "🟢 <b>No</b>" in out


def test_evm_proxy_authority_renders() -> None:
    r = _report()
    r["contract_authority"] = {"evm_proxy": {"is_proxy": True, "owner_renounced": False}}
    out = format_scan_report(r)
    assert "Proxy" in out
    assert "🟡 <b>proxy</b>" in out
    assert "Owner" in out
    assert "🔴 <b>ACTIVE</b>" in out
