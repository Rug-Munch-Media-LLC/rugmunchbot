"""Canonical ML deep-scan report (/deepscan — fraud-GNN/pyod/pygod)."""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._base import footer_links, sep, thin_sep
from rugmunchbot.formatters._scan_report_common import _collapse_blank_lines

_RISK_EMOJI: dict[str, str] = {
    "low": "\U0001f7e2",  # 🟢
    "medium": "\U0001f7e1",  # 🟠
    "high": "\U0001f7e0",  # 🟡
    "critical": "\U0001f534",  # 🔴
}


_FINDING_EMOJI: dict[str, str] = {
    "error": "\u274c",
    "warning": "\u26a0\ufe0f",
    "info": "\u2139\ufe0f",
}


_LEVEL_RANK: dict[str, int] = {"error": 3, "warning": 2, "info": 1}


_MAX_FINDINGS = 5


def _fmt_score(value: Any) -> str:
    """Format a 0..1 probability/score as a percentage string."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if num < 0 or num > 1:
        return f"{num:.2f}"
    return f"{num * 100:.1f}%"


def _fmt_tax(value: Any) -> str:
    """Format a tax percent (already 0..100 scale) or 'n/a'."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{num:.1f}%"


def _ml_verdict_sentence(overall: str, sell: dict[str, Any]) -> str:
    """One-line plain-language verdict sentence for the ML deep scan."""
    level = str(overall or "").lower()
    if level == "critical":
        return "critical ML signals \u2014 avoid; treat as a probable scam"
    if level == "high":
        return "multiple anomaly signals \u2014 high caution before any buy"
    if level == "medium":
        return "elevated ML risk \u2014 verify everything, size small"
    return "no major ML anomaly signals \u2014 normal caution applies"


def _ml_what_to_watch(overall: str, findings: list[dict[str, Any]]) -> str:
    """Short actionable "what to watch" line for the ML deep scan."""
    level = str(overall or "").lower()
    joined = " ".join(str(f.get("message") or "") for f in findings).lower()
    if "honeypot" in joined:
        return "Verify you can actually SELL before buying \u2014 honeypot signal from sell simulation."
    if "blacklist" in joined:
        return "Owner can blacklist wallets \u2014 holders can be frozen out mid-trade."
    if "dangerous function" in joined:
        return "Decompiled ABI exposes dangerous functions \u2014 treat the contract as hostile."
    if level == "critical":
        return "Critical ML verdict \u2014 avoid this token entirely."
    if level == "high":
        return "Multiple anomaly signals \u2014 watch for sudden exit liquidity pulls."
    if level == "medium":
        return "Elevated risk \u2014 watch taxes and sell liquidity before entering."
    return "Standard caution \u2014 size in small and watch sell pressure."


def format_deep_ml_scan_report(report: Any, analyst_read: str = "") -> str:
    """Canonical ML deep-scan report (``/deepscan`` — fraud-GNN/pyod/pygod).

    Renders a ``app.deep_scan.DeepScanReport`` using the shared scan-report
    skeleton: one-line verdict up top, a tight ML metrics table, sell-sim +
    findings as evidence bullets, and a "what to watch" line. HTML-safe —
    every data-derived string is escaped.
    """
    s = sep()
    d = thin_sep()
    overall = str(getattr(report, "overall_risk", "unknown") or "unknown").lower()
    scores = dict(getattr(report, "risk_scores", None) or {})
    sell = dict(getattr(report, "sell_sim", None) or {})
    decompiled = dict(getattr(report, "decompiled", None) or {})
    findings = list(getattr(report, "findings", None) or [])

    emoji = _RISK_EMOJI.get(overall, "\u26aa")
    lines: list[str] = [
        "\U0001f52c <b>Deep ML Scan</b>",
        s,
        f"<code>{html.escape(str(getattr(report, 'address', '')))}</code>",
        f"\u26d3\ufe0f Chain: <b>{html.escape(str(getattr(report, 'chain', '')))}</b>",
        "",
        # ── 1. One-line verdict up top ──
        f"{emoji} <b>Verdict: {html.escape(overall.upper())}</b> \u2014 {_ml_verdict_sentence(overall, sell)}",
    ]

    # ── 1b. AI Analyst Read (bundle/cluster plain-language interpretation) ──
    # Renders BEFORE the deterministic sections. Fail-open: an empty/absent
    # block (LLM down or no bundle data) is skipped entirely.
    if analyst_read:
        lines.extend(["", analyst_read])

    # ── 2. Key metrics as a tight bold table (ML-specific) ──
    fraud = scores.get("fraud_gnn") or {}
    pyod = scores.get("pyod") or {}
    pygod = scores.get("pygod") or {}

    def _score_row(label: str, entry: dict[str, Any], model_key: str) -> str:
        if entry.get("error"):
            return f"  {label:<16} <i>{html.escape(str(entry['error'])[:60])}</i>"
        raw_score = entry.get("fraud_probability") if "fraud_probability" in entry else entry.get("anomaly_score")
        score = _fmt_score(raw_score)
        model = html.escape(str(entry.get(model_key) or "unknown"))
        flag = " \u26a0\ufe0f anomaly" if entry.get("is_anomaly") or entry.get("is_fraud") else ""
        return f"  {label:<16} <b>{score}</b> ({model}){flag}"

    metrics: list[str] = []
    if fraud or fraud.get("error"):
        metrics.append(_score_row("fraud_gnn", fraud, "model"))
    if pyod or pyod.get("error"):
        metrics.append(_score_row("pyod", pyod, "model_type"))
    if pygod.get("model_type") == "skipped":
        metrics.append("  pygod            <i>skipped (no transfer graph)</i>")
    elif pygod or pygod.get("error"):
        metrics.append(_score_row("pygod", pygod, "model_type"))
    # Sell tax: pyrevm's tax_pct first, then the honeypot sell-sim sell_tax.
    metrics.append(
        f"  Sell-Sim Tax     <b>{_fmt_tax(sell.get('tax_pct') if sell.get('tax_pct') is not None else sell.get('sell_tax'))}</b>"
    )
    if decompiled.get("success"):
        metrics.append(f"  ABI Functions    <b>{len(decompiled.get('functions') or [])}</b>")
    metrics.append(f"  Findings         <b>{len(findings)}</b>")
    lines.extend(["", "\U0001f4ca <b>Key Metrics</b>", d, *metrics])

    # ── 3. Evidence bullets ──
    # Sell-sim flags (honeypot / blacklist / anti-whale) — hard evidence.
    lines.extend(["", "\U0001f4b8 <b>Sell Simulation</b>", d])
    # Honeypot sell-sim verdict (Honeypot.is / GoPlus) — the clear OK / BLOCKED
    # line. Honest + fail-open: "—" when no data source was reachable.
    verdict = str(sell.get("sell_sim_verdict") or "unavailable").upper()
    sim_source = html.escape(str(sell.get("source") or ""))
    if verdict in ("OK", "BLOCKED", "EXTREME_TAXES"):
        sim_tax = sell.get("sell_tax")
        if verdict == "OK":
            tax_txt = f" (tax {_fmt_tax(sim_tax)})" if sim_tax is not None else ""
            verdict_line = f"  \U0001f7e2 Sell sim OK{tax_txt}"
        else:
            tax_txt = f" ({_fmt_tax(sim_tax)} sell tax)" if sim_tax is not None else ""
            verdict_line = f"  \U0001f534 Sell sim BLOCKED / EXTREME taxes{tax_txt}"
        if sim_source:
            verdict_line += f" <i>({sim_source})</i>"
        lines.append(verdict_line)
    if sell.get("simulation_success"):
        flags: list[str] = []
        if sell.get("can_sell") and sell.get("sell_to_pair_reverted"):
            flags.append("\U0001f345 honeypot")
        if sell.get("blacklist_hit"):
            flags.append("\U0001f6ab blacklist")
        if sell.get("anti_whale_hit"):
            flags.append("\U0001f40b anti-whale")
        risk = html.escape(str(sell.get("risk") or "unknown"))
        lines.append(
            f"  \U0001f504 risk={risk} \u00b7 tax {_fmt_tax(sell.get('tax_pct'))}"
            + (f" \u00b7 {' '.join(flags)}" if flags else "")
        )
    elif verdict not in ("OK", "BLOCKED", "EXTREME_TAXES"):
        warn = (sell.get("warnings") or ["unavailable"])[0]
        lines.append(f"  <i>{html.escape(str(warn)[:80])}</i>")

    # Decompiled ABI summary.
    if decompiled.get("success"):
        lines.append(
            f"  \U0001f9e9 Decompiled ABI: <b>{len(decompiled.get('functions') or [])}</b> function(s) from bytecode"
        )
    else:
        err = decompiled.get("decompile_error") or "unavailable"
        lines.append(f"  \U0001f9e9 Decompiled ABI: <i>{html.escape(str(err)[:80])}</i>")

    # Findings — evidence bullets, worst first.
    sorted_findings = sorted(findings, key=lambda f: _LEVEL_RANK.get(str(f.get("level")), 0), reverse=True)
    lines.extend(["", "\U0001f50e <b>Findings</b>", d])
    if not sorted_findings:
        lines.append("  \u2705 No findings recorded")
    else:
        for f in sorted_findings[:_MAX_FINDINGS]:
            level = str(f.get("level") or "info")
            emoji_f = _FINDING_EMOJI.get(level, "\u2022")
            msg = html.escape(str(f.get("message") or ""))
            module = html.escape(str(f.get("module") or ""))
            lines.append(f"  {emoji_f} {msg} <i>({module})</i>")
        extra = len(sorted_findings) - _MAX_FINDINGS
        if extra > 0:
            lines.append(f"  <i>\u2026and {extra} more</i>")

    # ── 4. What to watch ──
    lines.extend(["", f"\U0001f440 <b>Watch:</b> {_ml_what_to_watch(overall, sorted_findings)}"])
    lines.append(footer_links())
    return _collapse_blank_lines("\n".join(lines))
