"""Wallet / holder intel sections for the scan report.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: the dedicated Deployer section and the GMGN
"Smart Money / Holders" section."""

from __future__ import annotations

import contextlib
import html
from typing import Any

from app.domains.telegram.rugmunchbot.formatters._base import explorer_url, thin_sep
from app.domains.telegram.rugmunchbot.formatters._scan_report_common import _short_addr
from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import _deployer_history_line
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "_format_deployer_section",
    "_format_gmgn_section",
]

# --- Scan-report intel helpers (own-data engines) ---


def _format_deployer_section(r: dict[str, Any]) -> list[str]:
    """Format a dedicated deployer info section — always shown in scan output.

    Shows: deployer address, launchpad detection, reputation label,
    deployer history (if repeat deployer), and RAG cross-reference.
    """
    lines = []
    d = thin_sep()

    deployer_addr = r.get("deployer_address") or r.get("dev_wallet") or ""
    reputation_label = r.get("reputation_label", "")
    reputation_score = r.get("reputation_score")
    deployer_profile = r.get("deployer_profile", {})
    chain = r.get("chain", "")

    # Header
    lines.append("")
    lines.append("\U0001f468\u200d\U0001f4bb <b>Deployer</b>")
    lines.append(d)

    # Deployer address (always shown) + block-explorer link when resolved.
    if deployer_addr and str(deployer_addr).lower() not in ("unknown", "null", "none"):
        lines.append(f"  \U0001f464 Address: <code>{html.escape(str(deployer_addr))}</code>")
        with contextlib.suppress(ValueError, TypeError):
            # malformed address/chain — keep the address, drop the link
            lines.append(
                f'  \U0001f50d <a href="{html.escape(explorer_url(str(deployer_addr), chain), quote=True)}">'
                "Explorer</a>"
            )
    else:
        lines.append("  \U0001f464 Address: <i>unknown</i>")

    # Launchpad detection
    try:
        from app.domains.scanner.engines.launchpad_registry import detect_launchpad

        launchpad = detect_launchpad(deployer_addr, chain)
        if launchpad:
            lp_name = html.escape(str(launchpad.get("name", "unknown")))
            lp_type = html.escape(str(launchpad.get("type", "")))
            lines.append(f"  \U0001f680 Launchpad: <b>{lp_name}</b> ({lp_type})")
    except (ConnectionError, OSError, ImportError) as e:
        logger.warning("launchpad detect failed: %s", e)

    # Reputation label
    if reputation_label and reputation_label != "UNKNOWN":
        emoji = "🔴" if reputation_label in ("CRITICAL", "HIGH") else "🟡" if reputation_label == "MEDIUM" else "🟢"
        lines.append(f"  {emoji} Reputation: <b>{html.escape(str(reputation_label))}</b>")

    # Reputation score (0-100, higher = more trusted)
    if reputation_score is not None:
        try:
            score = float(reputation_score)
            emoji = "🟢" if score >= 70 else "🟡" if score >= 40 else "🔴"
            lines.append(f"  {emoji} Trust Score: <b>{score:.0f}/100</b>")
        except (TypeError, ValueError) as e:
            logger.debug("deployer wallet parse failed: %s", e)

    # Deployer metrics (from own engine)
    deployer_metrics = r.get("deployer_metrics", {})
    if deployer_metrics:
        total_deployed = deployer_metrics.get("total_tokens_deployed", 0)
        rugpull_rate = deployer_metrics.get("rugpull_rate", 0.0)
        honeypot_rate = deployer_metrics.get("honeypot_rate", 0.0)
        if total_deployed:
            lines.append(f"  \U0001f4ca Tokens Deployed: <b>{total_deployed}</b>")
        if rugpull_rate:
            lines.append(f"  \U0001f4a3 Rugpull Rate: <b>{float(rugpull_rate) * 100:.0f}%</b>")
        if honeypot_rate:
            lines.append(f"  \U0001f52a Honeypot Rate: <b>{float(honeypot_rate) * 100:.0f}%</b>")

    # Repeat deployer profile — compact summary line plus extra profile stats
    if deployer_profile and isinstance(deployer_profile, dict):
        # Single source of truth for the repeat-deployer summary: the compact
        # "_deployer_history_line" line (tokens / rugged % / avg lifespan), so
        # the report never renders the same deployer stats twice.
        history_line = _deployer_history_line(r)
        if history_line:
            lines.append(f"  {history_line}")
        honeypot_pct = deployer_profile.get("honeypot_pct", 0.0)
        chains_used = deployer_profile.get("chains_used", [])
        label = deployer_profile.get("label", "")

        if honeypot_pct:
            try:
                hpct = float(honeypot_pct) * 100 if float(honeypot_pct) <= 1.0 else float(honeypot_pct)
                lines.append(f"  \U0001f52a Honeypot Rate: <b>{hpct:.0f}%</b>")
            except (TypeError, ValueError) as e:
                logger.debug("wallet snapshot parse failed: %s", e)
        if chains_used:
            lines.append(f"  \U0001f310 Chains: <b>{html.escape(', '.join(str(c) for c in chains_used))}</b>")
        if label:
            lines.append(f"  \U0001f3f7\ufe0f Label: <b>{html.escape(str(label))}</b>")

    # Deployment info
    deployed_at = r.get("deployed_at") or r.get("deployment_timestamp")
    if deployed_at:
        lines.append(f"  \U0001f4c5 Deployed: <b>{str(deployed_at)[:19]}</b>")

    deployment_tx = r.get("deployment_tx") or r.get("deployment_hash")
    if deployment_tx:
        lines.append(f"  \U0001f517 TX: <code>{str(deployment_tx)[:20]}...</code>")

    return lines


def _format_gmgn_section(r: dict[str, Any]) -> list[str]:
    """Compact GMGN "Smart Money / Holders" section (empty when no data).

    Consumes the ``gmgn`` sub-dict attached by
    ``app.domains.scanner.gmgn_enrich.enrich_scan_with_gmgn``::

        {
            "holders": {"top_holder_pct", "holder_count", "insider_count",
                        "sniper_count", "source"},
            "smart_money": {"net_bought", "smart_money_count", "kol_count",
                            "buy_pressure", "total_profit_pnl",
                            "top_smart_money_wallets": [{"address", "net",
                                                         "direction"}],
                            "source"},
        }

    Returns an empty list when the scan result carries no ``gmgn`` data so
    the report simply omits the section.
    """
    gmgn = r.get("gmgn")
    if not isinstance(gmgn, dict):
        return []
    holders = gmgn.get("holders") if isinstance(gmgn.get("holders"), dict) else {}
    smart = gmgn.get("smart_money") if isinstance(gmgn.get("smart_money"), dict) else {}
    if not holders and not smart:
        return []

    lines: list[str] = ["", "\U0001f4a1 <b>Smart Money / Holders</b>", thin_sep()]

    if holders:
        pct = holders.get("top_holder_pct")
        if pct is not None:
            try:
                pct_f = float(pct)
            except (TypeError, ValueError):
                pct_f = 0.0
            emoji = "\U0001f534" if pct_f >= 50 else "\U0001f7e1" if pct_f >= 30 else "\U0001f7e2"
            lines.append(f"  {emoji} Top-10 Supply: <b>{pct_f:.1f}%</b>")
        hcount = holders.get("holder_count")
        if hcount is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f465 Holders: <b>{int(hcount):,}</b>")
        flags = []
        if holders.get("insider_count"):
            flags.append(f"\U0001f575\ufe0f insiders {int(holders['insider_count'])}")
        if holders.get("sniper_count"):
            flags.append(f"\U0001f3af snipers {int(holders['sniper_count'])}")
        if flags:
            lines.append(f"  {' · '.join(flags)}")

    if smart:
        net = smart.get("net_bought")
        sm_count = int(smart.get("smart_money_count") or 0)
        if net is not None:
            try:
                net_f = float(net)
            except (TypeError, ValueError):
                net_f = 0.0
            emoji = "\U0001f7e2" if net_f > 0 else "\U0001f534" if net_f < 0 else "\u26aa"
            flow = f"{net_f:+.0f}"
            tracked = f" ({sm_count} tracked)" if sm_count else ""
            lines.append(f"  {emoji} Smart-Money Net: <b>{flow}</b>{tracked}")
        wallets = smart.get("top_smart_money_wallets")
        if isinstance(wallets, list):
            parts = []
            for w in wallets[:3]:
                if not isinstance(w, dict):
                    continue
                addr = str(w.get("address") or "")
                direction = html.escape(str(w.get("direction") or "MIXED"))
                if not addr:
                    continue
                arrow = {"BUY": "\U0001f7e2", "SELL": "\U0001f534", "MIXED": "\U0001f7e1"}.get(direction, "\u26aa")
                parts.append(f"{arrow} {html.escape(_short_addr(addr))} {direction}")
            if parts:
                lines.append(f"  \U0001f9e0 {' | '.join(parts)}")

    return lines
