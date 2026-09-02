"""New-scanner-engine metric rows for the /scan report (2026-08-26 push).

Renders the enrichment engines the scan API now returns top-level:
``dev_sold``, ``dex_paid``, ``price_metrics`` (ATH), ``sniper_count``,
``holder_concentration`` (gini/hhi), ``wallet_metrics`` (first-tx %) and
``contract_authority`` (Solana mint/freeze renunciation, EVM proxy).
Honest-data rule: every row renders only when the engine actually
reported the value — ``None``/``unavailable`` payloads render nothing.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._scan_report_common._fields import _float_or_none
from rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)

_THIN_SEP = "\u2500" * 24


def _engine_scanned(payload: Any) -> bool:
    """True when an engine payload exists and did not report 'unavailable'."""
    return isinstance(payload, dict) and str(payload.get("result") or "") != "unavailable"


def _dev_sold_row(r: dict[str, Any]) -> str | None:
    """Dev-sold verdict row — 🔴 sold / 🟢 not sold / None when no data."""
    ds = r.get("dev_sold")
    if not _engine_scanned(ds):
        return None
    sold = ds.get("dev_sold")
    if sold is None:
        return None
    bits: list[str] = []
    if sold:
        bits.append("\U0001f534 <b>YES</b>")
        sell_count = ds.get("sell_count")
        if isinstance(sell_count, int) and sell_count > 0:
            bits.append(f"{sell_count} sells")
        pct = _float_or_none(ds.get("total_sold_pct"))
        if pct is not None:
            bits.append(f"{pct:.1f}% of supply")
    else:
        bits.append("\U0001f7e2 <b>No</b>")
    return "  Dev Sold       " + " \u00b7 ".join(bits)


def _dex_paid_row(r: dict[str, Any]) -> str | None:
    """DEX-paid row — whether the deployer paid for DEX listings."""
    dp = r.get("dex_paid")
    if not _engine_scanned(dp):
        return None
    paid = dp.get("dex_paid")
    if paid is None:
        return None
    if paid:
        dexes = dp.get("dexes")
        names = ", ".join(str(d) for d in dexes[:3]) if isinstance(dexes, list) and dexes else ""
        n = dp.get("dex_count")
        count = f" \u00b7 <b>{n}</b>" if isinstance(n, int) and n else ""
        detail = f" ({html.escape(names)})" if names else ""
        return f"  DEX Paid       \U0001f7e2 <b>Yes</b>{count}{detail}"
    return "  DEX Paid       \U0001f7e2 <b>No</b>"


def _ath_row(r: dict[str, Any]) -> str | None:
    """ATH row from price_metrics — ATH value + drawdown from it."""
    pm = r.get("price_metrics")
    if not _engine_scanned(pm):
        return None
    ath = _float_or_none(pm.get("ath"))
    if ath is None:
        return None
    row = f"  ATH            \U0001f4c8 <b>${ath:,.6g}</b>"
    cur = _float_or_none(pm.get("current_price"))
    if cur is not None and ath > 0:
        row += f" ({(cur / ath - 1) * 100:+.0f}% from ATH)"
    chg = _float_or_none(pm.get("change_1h_pct"))
    if chg is not None:
        row += f" \u00b7 1h {chg:+.1f}%"
    return row


def _sniper_row(r: dict[str, Any]) -> str | None:
    """Sniper row from the own sniper_count engine (not the GMGN fallback)."""
    sc = r.get("sniper_count")
    if not _engine_scanned(sc):
        return None
    n = sc.get("sniper_count")
    if n is None:
        return None
    try:
        count = int(n)
    except (TypeError, ValueError):
        return None
    row = f"  Snipers        \U0001f3af <b>{count}</b>"
    pct = _float_or_none(sc.get("sniper_pct"))
    if pct is not None:
        row += f" ({pct:.1f}%)"
    block = sc.get("first_buy_block")
    if isinstance(block, int):
        row += f" \u00b7 first buy block {block}"
    return row


def _concentration_extra_rows(r: dict[str, Any]) -> list[str]:
    """Gini/HHI rows from holder_concentration (top-10 % renders elsewhere)."""
    hc = r.get("holder_concentration")
    if not isinstance(hc, dict):
        return []
    rows: list[str] = []
    gini = _float_or_none(hc.get("gini"))
    if gini is not None:
        label = "EXTREME" if gini >= 0.8 else "HIGH" if gini >= 0.6 else "MODERATE"
        rows.append(f"  Gini Coeff     \U0001f4ca <b>{gini:.2f}</b> ({label})")
    hhi = _float_or_none(hc.get("hhi"))
    if hhi is not None:
        band = "CONCENTRATED" if hhi >= 3000 else "MODERATE" if hhi >= 1500 else "HEALTHY"
        rows.append(f"  HHI            \U0001f4ca <b>{hhi:,.0f}</b> ({band})")
    holders = hc.get("total_holders")
    if isinstance(holders, int) and holders > 0:
        rows.append(f"  Holders (idx)  \U0001f465 <b>{holders:,}</b>")
    return rows


def _wallet_metrics_extra_rows(r: dict[str, Any]) -> list[str]:
    """first_tx_pct row from wallet_metrics (fresh/CEX render elsewhere)."""
    wm = r.get("wallet_metrics")
    if not _engine_scanned(wm):
        return []
    rows: list[str] = []
    ftx = _float_or_none(wm.get("first_tx_pct"))
    if ftx is not None:
        rows.append(f"  First Tx       \U0001f195 <b>{ftx:.1f}%</b> first-ever tx")
    analyzed = wm.get("holders_analyzed")
    if isinstance(analyzed, int) and analyzed > 0:
        rows.append(f"  Wallets Analyzed <b>{analyzed:,}</b>")
    return rows


def _authority_rows(r: dict[str, Any]) -> list[str]:
    """Authority rows from contract_authority (Solana or EVM proxy shape)."""
    ca = r.get("contract_authority")
    if not isinstance(ca, dict):
        return []
    rows: list[str] = []
    sol = ca.get("solana_authorities")
    if isinstance(sol, dict):
        for key, label in (("mint_authority", "Mint Auth"), ("freeze_authority", "Freeze Auth")):
            entry = sol.get(key)
            if not isinstance(entry, dict) or entry.get("is_renounced") is None:
                continue
            renounced = bool(entry["is_renounced"])
            emoji = "\U0001f7e2" if renounced else "\U0001f534"
            state = "renounced" if renounced else "ACTIVE"
            rows.append(f"  {label:<13}{emoji} <b>{state}</b>")
    evm = ca.get("evm_proxy")
    if isinstance(evm, dict):
        proxy = evm.get("is_proxy")
        if proxy is not None:
            emoji = "\U0001f7e1" if proxy else "\U0001f7e2"
            state = "proxy" if proxy else "no proxy"
            rows.append(f"  Proxy          {emoji} <b>{state}</b>")
        owner = evm.get("owner_renounced")
        if owner is not None:
            emoji = "\U0001f7e2" if owner else "\U0001f534"
            state = "renounced" if owner else "ACTIVE"
            rows.append(f"  Owner          {emoji} <b>{state}</b>")
    return rows


def _engine_metrics_rows(r: dict[str, Any]) -> list[str]:
    """Rows for the new scanner engines — appended to the Key Metrics table.

    Renders only what the engines actually reported; returns [] when none
    of the new payloads are present (older backend payloads unaffected).
    """
    rows: list[str] = []
    for row in (
        _dev_sold_row(r),
        _dex_paid_row(r),
        _ath_row(r),
        _sniper_row(r),
    ):
        if row:
            rows.append(row)
    rows.extend(_concentration_extra_rows(r))
    rows.extend(_wallet_metrics_extra_rows(r))
    rows.extend(_authority_rows(r))
    return rows


__all__ = ["_engine_metrics_rows"]
