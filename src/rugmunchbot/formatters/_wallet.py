"""Wallet-report + wallet-composition formatting for the RugMunch bot.

Extracted from ``formatters.py`` during the Batch 11 de-godding pass:
``format_wallet_report`` + its small helpers. The wallet-composition
section (``format_wallet_composition_section``) moved here from
``formatters/_sentinel.py`` during the Batch 13 de-godding pass.
``formatters/__init__.py`` re-exports every public name.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.rugmunchbot.config import _risk_gauge, fmt_chain, fmt_number, risk_bar
from app.domains.telegram.rugmunchbot.formatters._base import (
    explorer_url,
    footer_links,
    format_funding_analysis,
    sep,
    thin_sep,
)
from app.domains.telegram.rugmunchbot.formatters._scan_report import _clip
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "_activity_score",
    "_explorer_link",
    "format_wallet_composition_section",
    "format_wallet_report",
]


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
    age_days = r.get("wallet_age_days")
    age_str = f"{age_days} days" if age_days else "Unknown"
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
    _raw_trust = r.get("trust")
    trust = _raw_trust if isinstance(_raw_trust, dict) else {}
    trust_line = trust.get("trust_line") if trust.get("trust_line") not in (None, "\u2014") else None
    if trust_line:
        lines.append(f"\U0001f511 <b>Trust:</b> {trust_line}")
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
            lines.append(f"  {i}. <b>{t.get('symbol', '???')}</b> - {fmt_number(t.get('value_usd', 0))}")
    if r["funding_source"]:
        lines.extend(["", f"\U0001f3e6 <b>Funding Source:</b> {r['funding_source']}"])
    if r["connected_wallets"]:
        lines.extend(["", "\U0001f517 <b>Connected Wallets</b>", d])
        for w in r["connected_wallets"][:5]:
            lines.append(f"  \u2022 <code>{w}</code>")
    if r.get("risk_score", 0) > 0:
        lines.extend(["", risk_bar(r["risk_score"])])
        lines.append(f"  {_risk_gauge(r['risk_score'])}")

    # v2 wallet metrics: entity, scam exposure, smart money overlap
    v2_lines = []
    if r.get("entity_name"):
        v2_lines.append(f"\U0001f3e6 Entity: <b>{r['entity_name']}</b> ({r.get('entity_type', 'unknown')})")
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
        v2_lines.append(f"\U0001f52e Forecast: {_clip(r['risk_forecast'], 120)}")
    if v2_lines:
        lines.extend(["", "\U0001f9e9 <b>Wallet Intelligence</b>", d])
        lines.extend(v2_lines)

    funding_text = format_funding_analysis(r)
    if funding_text:
        lines.append(funding_text)

    _addr = r.get("address", r.get("token", ""))
    _chain = r.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return "\n".join(lines)


# --- Wallet composition section (for /deepscan) ---


def format_wallet_composition_section(r: dict[str, Any], token_created_ts: str | None = None) -> str:
    """Full wallet-composition block for /deepscan (own funding_analysis engine).

    Combines ``wallet_metrics`` (fresh <30d / CEX-funded / first-tx-this-token)
    with the ``funding_analysis`` evidence (mixer-funded %, risk assessment).
    Returns an empty string when neither payload has data — deepscan simply
    omits the section.

    Coverage honesty: ``wallet_metrics.coverage_start_ts`` is the start of
    the indexer's transfer window for this token. When the token's creation
    time (``token_created_ts``, from DexScreener) predates coverage by >7
    days, the indexer missed the token's early history and every holder
    looks <30 days old — fresh/brand-new percentages are artifacts. In that
    case only the label-based CEX line renders, under a data-coverage caveat.
    """
    wm = r.get("wallet_metrics") if isinstance(r.get("wallet_metrics"), dict) else {}
    if wm.get("result") == "unavailable":  # type: ignore[union-attr]  # PTB None-able fields
        wm = {}
    funding = (r.get("evidence") or {}).get("funding_analysis") or {}
    if not isinstance(funding, dict):
        funding = {}

    def _f(val: object) -> float | None:
        try:
            return float(val)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    cex = _f(wm.get("cex_funded_pct"))  # type: ignore[union-attr]  # PTB None-able fields
    analyzed = int(wm.get("holders_analyzed") or 0)  # type: ignore[union-attr]  # PTB None-able fields
    scope = f" (top {analyzed} holders)" if analyzed else ""

    # ── Coverage-gap branch: fresh/brand-new are indexer artifacts ──
    coverage_iso = wm.get("coverage_start_ts")  # type: ignore[union-attr]  # PTB None-able fields
    if cex is not None and coverage_iso and token_created_ts:
        try:
            import datetime as _dt

            cov = _dt.datetime.fromisoformat(str(coverage_iso).replace("Z", "+00:00"))
            created = _dt.datetime.fromisoformat(str(token_created_ts).replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=_dt.UTC)
            if cov.tzinfo is None:
                cov = cov.replace(tzinfo=_dt.UTC)
            gap_days = (cov - created).total_seconds() / 86400.0
            if gap_days > 7:
                lines = [
                    "\U0001f45b <b>Wallet Composition</b>" + scope,
                    thin_sep(),
                    (
                        f"\u26a0\ufe0f Transfer coverage starts "
                        f"{cov.date().isoformat()} (token created {created.date().isoformat()}) — "
                        "fresh/brand-new stats skipped, indexer missed early history."
                    ),
                ]
                if cex is not None:
                    lines.append(f"\U0001f3e6 Exchange-funded (CEX, label-based): <b>{cex:.1f}%</b>")
                return "\n".join(lines)
        except (TypeError, ValueError) as e:
            logger.debug("unparseable dates — fall through: %s", e)

    fresh = _f(wm.get("fresh_wallet_pct"))  # type: ignore[union-attr]  # PTB None-able fields
    first_tx = _f(wm.get("first_tx_pct"))  # type: ignore[union-attr]  # PTB None-able fields
    mixer = _f(funding.get("mixer_funded_pct"))
    risk = str(funding.get("risk_assessment") or "").upper()

    lines = []
    if fresh is not None:
        lines.append(f"\U0001f195 Fresh wallets (<30d): <b>{fresh:.1f}%</b>")
    if first_tx is not None:
        lines.append(f"\u2728 Brand-new (1st tx = this token): <b>{first_tx:.1f}%</b>")
    if cex is not None:
        lines.append(f"\U0001f3e6 Exchange-funded (CEX): <b>{cex:.1f}%</b>")
    if mixer:
        lines.append(f"\U0001f977 Mixer-funded: <b>{mixer:.1f}%</b>")
    if risk and risk not in ("", "UNKNOWN"):
        emoji = {"LOW": "\U0001f7e2", "MEDIUM": "\U0001f7e1", "HIGH": "\U0001f534", "CRITICAL": "\U0001f6a8"}.get(
            risk, "\u26aa"
        )
        lines.append(f"{emoji} Funding risk: <b>{risk}</b>")
    if not lines:
        return ""
    return "\U0001f45b <b>Wallet Composition</b>" + scope + "\n" + thin_sep() + "\n" + "\n".join(lines)
