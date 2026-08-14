"""Wallet-report formatting for the RugMunch bot.

Extracted from ``formatters.py`` during the Batch 11 de-godding pass:
``format_wallet_report`` + its small helpers. ``formatters.py`` re-exports
the public name.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import _risk_gauge, fmt_chain, fmt_number, risk_bar
from rugmunchbot.formatters._base import (
    explorer_url,
    footer_links,
    format_funding_analysis,
    sep,
    thin_sep,
)


# --- Wallet report formatting ---
def _activity_score(tx_count: int | None) -> str:
    if not tx_count or tx_count == 0:
        return "\U0001f7e0 None"
    if tx_count < 10:
        return "\U0001f7e1 Low"
    if tx_count < 100:
        return "\U0001f7e2 Medium"
    if tx_count < 1000:
        return "\U0001f535 High"
    return "\U0001f48e Very High"


def _explorer_link(address: str, chain: str) -> str:
    url = explorer_url(address, chain)
    if chain.lower() == "solana":
        return f'<a href="{url}">\U0001f50d Solscan</a>'
    domain = url.split("//")[1].split("/")[0]
    return f'<a href="{url}">\U0001f50d {domain.split(".")[0].title()}</a>'


def format_wallet_report(r: dict[str, Any]) -> str:
    s = sep()
    d = thin_sep()
    age_str = f"{r['wallet_age_days']} days" if r["wallet_age_days"] else "Unknown"
    tx_count = r.get("tx_count")
    tx_str = (
        f"\U0001f4dd Transactions: <b>{tx_count:,}</b>"
        if tx_count is not None and tx_count > 0
        else "\U0001f4dd Transactions: N/A"
    )
    score_str = _activity_score(tx_count)
    explorer_str = _explorer_link(r["address"], r["chain"])
    lines = [
        "\U0001f9db <b>Wallet Analysis</b>",
        s,
        f"<code>{r['address']}</code>",
        f"{fmt_chain(r['chain'])} | Age: {age_str} | {explorer_str}",
        "",
        "\U0001f4ca <b>Overview</b>",
        d,
        f"\U0001f4b0 Balance: <b>{fmt_number(r['balance_usd'])}</b>",
        tx_str,
        f"\U0001f4ca Activity Score: {score_str}",
        f"\U0001f3f7\ufe0f Type: {'Exchange' if r['is_exchange'] else 'Contract' if r['is_contract'] else 'EOA (Wallet)'}",
    ]
    if r["is_fresh"]:
        lines.append("\U0001f195 <b>FRESH WALLET</b> (<7 days) - \u26a0\ufe0f Higher risk")
    if r["flags"]:
        lines.extend(["", "\u26a0\ufe0f <b>Flags</b>", d])
        for flag in r["flags"]:
            lines.append(f"  \U0001f534 {flag}")
    if r["pnl_estimate"] is not None:
        pnl_e = "\U0001f4c8" if r["pnl_estimate"] >= 0 else "\U0001f4c9"
        lines.extend(["", f"{pnl_e} <b>Estimated P&L:</b> {fmt_number(r['pnl_estimate'])}"])
    if r["top_tokens"]:
        lines.extend(["", "\U0001fa99 <b>Top Holdings</b>", d])
        for i, t in enumerate(r["top_tokens"][:5], 1):
            lines.append(
                f"  {i}. <b>{t.get('symbol', '???')}</b> - {fmt_number(t.get('value_usd', 0))}"
            )
    if r["funding_source"]:
        lines.extend(["", f"\U0001f3e6 <b>Funding Source:</b> {r['funding_source']}"])
    if r["connected_wallets"]:
        lines.extend(["", "\U0001f517 <b>Connected Wallets</b>", d])
        for w in r["connected_wallets"][:5]:
            lines.append(f"  \u2022 <code>{w}</code>")
    if r["risk_score"] > 0:
        lines.extend(["", risk_bar(r["risk_score"])])
        lines.append(f"  {_risk_gauge(r['risk_score'])}")

    # v2 wallet metrics: entity, scam exposure, smart money overlap
    v2_lines = []
    if r.get("entity_name"):
        v2_lines.append(
            f"\U0001f3e6 Entity: <b>{r['entity_name']}</b> ({r.get('entity_type', 'unknown')})"
        )
    if r.get("persona"):
        conf = f" ({r.get('persona_confidence', 0)}% conf)" if r.get("persona_confidence") else ""
        v2_lines.append(f"\U0001f9d0 Persona: {r['persona']}{conf}")
    if r.get("scam_exposure_pct"):
        emoji = (
            "\U0001f534"
            if r["scam_exposure_pct"] > 20
            else "\U0001f7e1"
            if r["scam_exposure_pct"] > 5
            else "\U0001f7e2"
        )
        v2_lines.append(
            f"{emoji} Scam Exposure: <b>{r['scam_exposure_pct']:.0f}%</b> "
            f"({r.get('scam_tokens_held', 0)} scam token(s) held)"
        )
    if r.get("smart_money_overlap"):
        sm = ", ".join(str(s) for s in r["smart_money_overlap"][:5])
        v2_lines.append(f"\U0001f4b0 Smart Money Overlap: {sm}")
    if r.get("behavior_patterns"):
        bp = ", ".join(str(b) for b in r["behavior_patterns"][:4])
        v2_lines.append(f"\U0001f50e Behavior: {bp}")
    if r.get("risk_forecast"):
        v2_lines.append(f"\U0001f52e Forecast: {r['risk_forecast'][:120]}")
    if v2_lines:
        lines.extend(["", "\U0001f9e9 <b>v2 Wallet Intel</b>", d])
        lines.extend(v2_lines)

    funding_text = format_funding_analysis(r)
    if funding_text:
        lines.append(funding_text)

    _addr = r.get("address", r.get("token", ""))
    _chain = r.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return "\n".join(lines)
