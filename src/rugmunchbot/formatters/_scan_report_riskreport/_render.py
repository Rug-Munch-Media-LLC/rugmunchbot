"""Telegram-HTML renderer for the RiskReport section.

Split out of ``formatters/_scan_report_riskreport.py`` during the de-godding
pass. Renders the RiskReport dict as Telegram-HTML lines — headline, section
one-liners, top-risk bullets (concise) or the full ~30-factor table + deeper
analysis (deep). Pure module: no network, no I/O.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._scan_report_common._engines import _engine_metrics_rows
from rugmunchbot.formatters._scan_report_riskreport._constants import (
    _MIDDOT,
    _MIN_KNOWN_FOR_DETERMINATION,
    _short,
)

# ═══════════════════════════════════════════════════════════════════════════
# RENDERER (Telegram HTML)
# ═══════════════════════════════════════════════════════════════════════════

_DETERMINATION_EMOJI = {
    "scam": "\U0001f6a8",
    "rug": "\U0001f534",
    "risky": "\U0001f7e0",
    "ok": "\U0001f7e2",
    "unknown": "\u26aa",
}

_STATUS_EMOJI = {
    "critical": "\U0001f534",
    "risk": "\U0001f7e1",
    "ok": "\U0001f7e2",
    "unknown": "\u26aa",
}


#: Section one-liners for the concise renderer (key → (emoji, extractor)).


def _section_lines(rep: dict[str, Any]) -> list[str]:
    """The full-product one-liners: deployer / holders / first buyer / top-25 /
    fresh% / CEX% / bundle / cluster / honeypot / liquidity."""
    lines: list[str] = []

    dep = rep.get("deployer") or {}
    d_bits: list[str] = []
    if dep.get("address"):
        d_bits.append(f"<code>{html.escape(_short(dep['address']))}</code>")
    if dep.get("total_deployments"):
        d_bits.append(f"{dep['total_deployments']} tokens")
    if dep.get("repeat_deployer") is not None:
        d_bits.append("repeat=" + ("\u2713" if dep["repeat_deployer"] else "\u2717"))
    if dep.get("previously_rugged") is not None:
        d_bits.append("rugged_before=" + ("\u2713" if dep["previously_rugged"] else "\u2717"))
    if d_bits:
        lines.append(f"  \U0001f468\u200d\U0001f4bb <b>Deployer:</b> {_MIDDOT.join(d_bits)}")

    hol = rep.get("holders") or {}
    h_bits: list[str] = []
    if hol.get("total_holders") is not None:
        h_bits.append(f"{hol['total_holders']:,} holders")
    if hol.get("top_10_pct") is not None:
        h_bits.append(f"top-10 {hol['top_10_pct']}%")
    fresh = rep.get("fresh_wallet_pct")
    if fresh is not None:
        h_bits.append(f"fresh {fresh:.0f}%")
    cex = rep.get("cex_funded_wallet_pct")
    if cex is not None:
        h_bits.append(f"CEX-funded {cex:.0f}%")
    if h_bits:
        lines.append(f"  \U0001f465 <b>Holders:</b> {_MIDDOT.join(h_bits)}")

    fb = rep.get("first_buyer") or {}
    if fb.get("address"):
        fb_bits = [f"<code>{html.escape(_short(fb['address']))}</code>"]
        if fb.get("is_deployer") is not None:
            fb_bits.append("deployer=" + ("\u2713" if fb["is_deployer"] else "\u2717"))
        if fb.get("in_bundle") is not None:
            fb_bits.append("in_bundle=" + ("\u2713" if fb["in_bundle"] else "\u2717"))
        if fb.get("cohort_size") is not None:
            fb_bits.append(f"cohort {fb['cohort_size']}")
        lines.append(f"  \U0001f6d2 <b>First buyer:</b> {_MIDDOT.join(fb_bits)}")

    top25_pct = hol.get("top_25_pct")
    if top25_pct is not None:
        lines.append(f"  \U0001f3c6 <b>Top-25:</b> {top25_pct:.1f}% of tracked supply")

    bun = rep.get("bundle") or {}
    if bun.get("is_bundled") is not None:
        b_str = "\U0001f6a8 DETECTED" if bun["is_bundled"] else "not detected"
        if bun.get("launch_cluster_score") is not None:
            b_str += f" (cluster {bun['launch_cluster_score']:.0f}/100)"
        lines.append(f"  \U0001f578\ufe0f <b>Bundle:</b> {b_str}")

    cl = rep.get("cluster") or {}
    if isinstance(cl, dict) and cl.get("cluster_risk_score") is not None:
        lines.append(
            f"  \U0001f50e <b>Cluster:</b> {cl['cluster_risk_score']}/100 "
            f"({html.escape(str(cl.get('cluster_risk_label') or 'unknown').upper())})"
        )

    hp = rep.get("honeypot") or {}
    hp_bits: list[str] = []
    if hp.get("is_honeypot") is not None:
        hp_bits.append("\U0001f6a8 HONEYPOT" if hp["is_honeypot"] else "Not a honeypot")
    if hp.get("mintable") is not None:
        hp_bits.append("mint=" + ("\u2713" if hp["mintable"] else "\u2717"))
    if hp.get("freezable") is not None:
        hp_bits.append("freeze=" + ("\u2713" if hp["freezable"] else "\u2717"))
    if hp.get("buy_tax") is not None or hp.get("sell_tax") is not None:
        hp_bits.append(f"tax {hp.get('buy_tax') or 0:.0f}/{hp.get('sell_tax') or 0:.0f}%")
    if hp_bits:
        lines.append(f"  \U0001f6e1\ufe0f <b>Honeypot:</b> {_MIDDOT.join(hp_bits)}")

    liq = rep.get("liquidity") or {}
    liq_bits: list[str] = []
    if liq.get("locked_pct") is not None:
        liq_bits.append(f"locked {liq['locked_pct']:.0f}%")
    if liq.get("liquidity_usd") is not None:
        liq_bits.append(f"${liq['liquidity_usd']:,.0f}")
    if liq_bits:
        lines.append(f"  \U0001f4a7 <b>Liquidity:</b> {_MIDDOT.join(liq_bits)}")

    # 2026-09-02 scans-channel fix: the new scanner engines (dev_sold /
    # dex_paid / ATH / snipers / gini-HHI / first-tx% / mint-freeze
    # authority) render here too — not just in the /scan report layout — so
    # @rmiscans cards show the full product. Renders only what the engines
    # actually reported (honest-data rule; [] when none present).
    lines.extend(_engine_metrics_rows(rep))

    return lines


def _headline_lines(rep: dict[str, Any]) -> list[str]:
    """Composite score + determination + coverage header lines.

    Honest-output guard: a "Composite Risk: 0/100 OK · 1/30 factors known"
    headline is self-contradictory and misleads the reader (a low composite
    built from a handful of known factors is not evidence of a safe token).
    When fewer than ``_MIN_KNOWN_FOR_DETERMINATION`` factors are known and no
    decisive scam is confirmed, do NOT present a composite verdict — say the
    scan is pending instead. This keeps the header score and the body
    composite in agreement for deep scans too (the concise block is already
    gated by :func:`_concise_report_postable`).
    """
    composite = rep.get("composite_score")
    determination = str(rep.get("determination") or "unknown")
    dq = rep.get("data_quality") or {}
    try:
        _known = int(dq.get("factors_known") or 0)
    except (TypeError, ValueError):
        _known = 0
    try:
        _total = int(dq.get("factors_total") or 0)
    except (TypeError, ValueError):
        _total = 0
    if _known < _MIN_KNOWN_FOR_DETERMINATION and determination != "scam":
        return [
            "  \u26a0\ufe0f <b>Insufficient data \u2014 scan pending</b>",
            f"  \U0001f4ca {_known}/{_total} factors known "
            f"(a verdict needs \u2265 {_MIN_KNOWN_FOR_DETERMINATION})",
        ]
    emoji = _DETERMINATION_EMOJI.get(determination, "\u26aa")
    comp_str = f"<b>{composite}/100</b>" if composite is not None else "<b>unknown</b>"
    coverage = (
        f"{dq.get('factors_known', 0)}/{dq.get('factors_total', 0)} factors known "
        f"({dq.get('coverage_pct', 0.0)}%)"
    )
    lines = [
        f"  \U0001f3af <b>Composite Risk:</b> {comp_str} \u00b7 {emoji} <b>{html.escape(determination.upper())}</b>",
        f"  \U0001f4ca {coverage}",
    ]
    if rep.get("summary"):
        lines.append(f"  \U0001f4a1 {html.escape(_clip(rep['summary'], 220))}")
    return lines


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "\u2026"


def _top_factor_lines(rep: dict[str, Any], limit: int = 4) -> list[str]:
    """Top critical/risk factor bullets (basic scans; empty when none fired)."""
    factors = rep.get("factors") or []
    bad = [
        f
        for f in factors
        if isinstance(f, dict)
        and f.get("status") in ("critical", "risk")
        and f.get("risk_score") is not None
    ]
    if not bad:
        return []
    lines: list[str] = []
    for f in bad[:limit]:
        status = f.get("status")
        emoji = _STATUS_EMOJI.get(str(status), "\u26aa")
        score = f.get("risk_score")
        lines.append(
            f"  {emoji} {html.escape(str(f.get('label') or f.get('key')))} "
            f"<b>{score:.0f}/100</b> \u2014 {html.escape(_clip(str(f.get('explanation') or ''), 160))}"
        )
    return lines


def _factor_table_lines(rep: dict[str, Any]) -> list[str]:
    """Full ~30-factor table (deep scans)."""
    factors = rep.get("factors") or []
    if not factors:
        return ["  \u26aa <i>factor table unavailable</i>"]
    lines: list[str] = []
    for f in factors:
        if not isinstance(f, dict):
            continue
        status = str(f.get("status") or "unknown")
        emoji = _STATUS_EMOJI.get(status, "\u26aa")
        label = html.escape(str(f.get("label") or f.get("key") or "unknown"))
        score = f.get("risk_score")
        score_str = f"<b>{score:.0f}/100</b>" if score is not None else "\u2014"
        lines.append(f"  {emoji} {label:<36} {score_str:>8}")
    return lines


def _deeper_lines(rep: dict[str, Any]) -> list[str]:
    """Deeper-level per-section narrative (deep scans)."""
    deeper = rep.get("deeper_analysis")
    if not isinstance(deeper, dict) or not deeper:
        return []
    lines: list[str] = []
    for section, payload in deeper.items():
        if not isinstance(payload, dict):
            continue
        summary = str(payload.get("summary") or "").strip()
        if not summary:
            continue
        lines.append(f"  \u25ba {html.escape(str(section).upper())}")
        lines.append(f"      {html.escape(_clip(summary, 300))}")
        for ln in payload.get("factors") or []:
            if isinstance(ln, str) and ln.strip():
                lines.append(f"      {html.escape(_clip(ln.strip(), 240))}")
    return lines


def _render_risk_report(rep: dict[str, Any], *, deep: bool) -> list[str]:
    """Render the RiskReport dict as Telegram-HTML lines."""
    lines = _headline_lines(rep)
    lines.append("")
    lines.extend(_section_lines(rep))
    if deep:
        table = _factor_table_lines(rep)
        if table:
            lines.extend(["", f"  \U0001f4ca <b>Factors ({len(rep.get('factors') or [])})</b>"])
            lines.extend(table)
        deeper = _deeper_lines(rep)
        if deeper:
            lines.extend(["", "  \U0001f9e0 <b>Deeper Analysis</b>"])
            lines.extend(deeper)
        if rep.get("ai_assisted"):
            lines.append("  \U0001f916 <i>analysis AI-assisted</i>")
    else:
        top = _top_factor_lines(rep)
        if top:
            lines.extend(["", "  \u26a0\ufe0f <b>Top Risks</b>"])
            lines.extend(top)
    return lines
