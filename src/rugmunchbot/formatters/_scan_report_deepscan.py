"""SENTINEL deep-forensic scan report.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: the deep scan section (from
``POST /api/v1/x402-tools/sentinel_scan``) plus its standalone wrapper.

Canonical advanced layout (Batch 2026-08-16): every deep-scan report shares
the scan-report skeleton — one-line verdict up top, key metrics as a tight
bold table, evidence bullets, and a short "what to watch" line. The ML deep
scan (``handlers/deepscan.py``) delegates to ``format_deep_ml_scan_report``
here so there is exactly ONE deep-scan formatter path."""

from __future__ import annotations

import asyncio
import contextlib
import html
from typing import Any

from app.domains.telegram.rugmunchbot.config import fmt_chain
from app.domains.telegram.rugmunchbot.formatters._base import footer_links, sep, thin_sep
from app.domains.telegram.rugmunchbot.formatters._scan_report_common import _clip, _collapse_blank_lines
from app.domains.telegram.rugmunchbot.formatters._scan_report_risk import _format_risk_signals_section
from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import (
    SENTINEL_MODULE_EMOJI,
    _risk_level_emoji,
    _sentinel_recommendations,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)


__all__ = [
    "format_deep_ml_scan_report",
    "format_sentinel_deepscan_section",
    "format_sentinel_scan_report",
    "render_analyst_read_block",
]

# --- SENTINEL deep scan report (from POST /api/v1/x402-tools/sentinel_scan) ---

#: Overall-risk level → emoji for the ML deep scan.
_RISK_EMOJI: dict[str, str] = {
    "low": "\U0001f7e2",  # 🟢
    "medium": "\U0001f7e1",  # 🟠
    "high": "\U0001f7e0",  # 🟡
    "critical": "\U0001f534",  # 🔴
}

#: Finding level → emoji.
_FINDING_EMOJI: dict[str, str] = {
    "error": "\u274c",
    "warning": "\u26a0\ufe0f",
    "info": "\u2139\ufe0f",
}

#: Severity rank for sorting findings (higher = shown first).
_LEVEL_RANK: dict[str, int] = {"error": 3, "warning": 2, "info": 1}

#: Max findings rendered inline; the rest collapse to "+N more…".
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


# ─────────────────────────────────────────────────────────────────────────────
# AI "Analyst Read" — plain-language bundle/cluster interpretation
# ─────────────────────────────────────────────────────────────────────────────
# The "beyond competitors" differentiator: most scanners print a raw
# "bundle: N wallets" line; we hand the compact bundle/cluster intel to the
# snappy LLM and let it explain WHY the structure matters in plain English.
# Fail-open by design: empty intel, LLM timeout or blank answer all return ""
# so the caller simply skips the block (never a blank section).

#: Hard cap for the Analyst Read LLM call (snappy tier) — never stall a report.
_ANALYST_READ_TIMEOUT = 8.0
#: Max chars of rendered LLM output (LLM text is untrusted — always escaped).
_ANALYST_READ_MAX_CHARS = 900

_ANALYST_READ_SYSTEM = (
    "You are the RugMunch deep-scan analyst. Write 2-3 SHORT plain-English "
    "sentences interpreting a token's bundle/cluster structure for a trader. "
    "Use ONLY the facts provided in the intel JSON — never invent numbers, "
    "addresses or claims. Explain what the structure suggests (e.g. coordinated "
    "launch vs organic demand) and why it matters. No markdown, no emojis, no "
    "headers, no apologies."
)


def _build_bundle_intel(data: dict[str, Any]) -> dict[str, Any]:
    """Compact bundle/cluster intel summary for the Analyst Read prompt.

    Pulls ONLY values the scan actually reported — absent values are omitted
    so the LLM never sees fabricated zeros. Reads the SENTINEL deepscan dict
    shape: ``bundle_detection`` (with ``same_slot``), ``wallet_map``,
    ``holder_analysis`` and ``contract_creation``.
    """
    bundle = _as_dict(data, "bundle_detection")
    same_slot = _as_dict(bundle, "same_slot")
    wallet_map = _as_dict(data, "wallet_map")
    holder = _as_dict(data, "holder_analysis")
    creation = _as_dict(data, "contract_creation")
    cluster = _as_dict(data, "cluster_analysis")

    intel: dict[str, Any] = {}

    # Bundle wallet count: prefer the own-engine bundle count, then the
    # SENTINEL deployer-funded + sniper lists, then the total bundled %.
    n_same_bundle = _safe_int(same_slot.get("bundle_wallets"))
    n_deployer = len(bundle.get("deployer_funded") or [])
    n_snipers = len(bundle.get("same_block_snipers") or [])
    n_bundle = n_same_bundle or (n_deployer + n_snipers) or None
    if n_bundle:
        intel["bundle_wallet_count"] = n_bundle
    # Same-slot concentration (Solana, Helius-backed).
    n_slot = _safe_int(same_slot.get("same_slot_wallets"))
    if n_slot:
        intel["same_slot_wallets"] = n_slot
    sol_spent = _safe_float(same_slot.get("same_slot_sol_spent"))
    if sol_spent:
        intel["same_slot_sol_spent"] = round(sol_spent, 2)
    held_pct = _safe_float(same_slot.get("same_slot_held_pct"))
    if held_pct:
        intel["same_slot_held_pct"] = round(held_pct, 1)
    # Wallet roles (dev/whale/fish/shrimp/fresh/bundle counts).
    roles = wallet_map.get("roles")
    if isinstance(roles, dict):
        present = {str(k): int(v) for k, v in roles.items() if _safe_int(v)}
        if present:
            intel["wallet_roles"] = present
    # Deployer funding source (SENTINEL contract-creation module).
    dfp = _safe_float(creation.get("deployer_funded_percentage"))
    if dfp is not None:
        intel["deployer_funded_pct"] = round(dfp, 1)
    # Holder concentration.
    top10 = _safe_float(holder.get("top_10_percentage"))
    hhi = _safe_float(holder.get("hhi"))
    if top10 is not None or hhi is not None:
        conc: dict[str, Any] = {}
        if top10 is not None:
            conc["top10_pct"] = round(top10, 1)
        if hhi is not None:
            conc["hhi"] = round(hhi, 0)
        intel["holder_concentration"] = conc

    # Buyer timing (own wallet_map engine): fresh-wallet % + recently-funded %
    # among the first-buyers. Absent values are omitted — never fabricated 0%.
    timing = _as_dict(wallet_map, "buyer_timing")
    fresh_pct = _safe_float(timing.get("fresh_wallet_pct"))
    funded_pct = _safe_float(timing.get("recently_funded_pct"))
    if fresh_pct is not None or funded_pct is not None:
        timing_intel: dict[str, Any] = {}
        if fresh_pct is not None:
            timing_intel["fresh_wallet_pct"] = round(fresh_pct, 0)
        if funded_pct is not None:
            timing_intel["recently_funded_pct"] = round(funded_pct, 0)
        fw_h = _safe_int(timing.get("fresh_window_hours"))
        ff_h = _safe_int(timing.get("funded_window_hours"))
        if fw_h:
            timing_intel["fresh_window_hours"] = fw_h
        if ff_h:
            timing_intel["funded_window_hours"] = ff_h
        intel["buyer_timing"] = timing_intel

    # Funding-source clusters (own cluster_analysis engine): funder→wallet
    # grouping + the coordinated / organic verdict. Only surfaced when the
    # engine actually scanned (result == "scanned").
    if str(cluster.get("result")) == "scanned":
        cluster_intel: dict[str, Any] = {}
        n_funders = _safe_int(cluster.get("distinct_funders"))
        n_wallets = _safe_int(cluster.get("funded_wallets"))
        largest = _safe_int(cluster.get("largest_cluster_size"))
        coord = _safe_int(cluster.get("coordinated_buyers"))
        chain_b = _safe_int(cluster.get("funding_chain_buyers"))
        verdict = str(cluster.get("verdict") or "")
        if n_funders:
            cluster_intel["distinct_funders"] = n_funders
        if n_wallets:
            cluster_intel["funded_wallets"] = n_wallets
        if largest:
            cluster_intel["largest_cluster_size"] = largest
        if coord:
            cluster_intel["coordinated_buyers"] = coord
        if chain_b:
            cluster_intel["funding_chain_buyers"] = chain_b
        if verdict:
            cluster_intel["cluster_verdict"] = verdict
        # Deeper dimensions (2-hop chain, supply grab, time nodes) — only
        # surfaced when the engine measured them.
        depth = _safe_int(cluster.get("funding_chain_depth"))
        if depth:
            cluster_intel["funding_chain_depth"] = depth
        two_hop = _safe_int(cluster.get("two_hop_chain_buyers"))
        if two_hop:
            cluster_intel["two_hop_chain_buyers"] = two_hop
        time_nodes = _safe_int(cluster.get("time_nodes"))
        if time_nodes:
            cluster_intel["time_nodes"] = time_nodes
        supply_grab = cluster.get("supply_grab")
        if isinstance(supply_grab, dict) and str(supply_grab.get("status") or "none") in ("holding", "dumped"):
            cluster_intel["supply_grab"] = {
                "status": str(supply_grab.get("status")),
                "held_pct": round(_safe_float(supply_grab.get("held_pct")) or 0.0, 1),
                "dumped_pct": round(_safe_float(supply_grab.get("dumped_pct")) or 0.0, 1),
            }
        if cluster_intel:
            intel["funding_clusters"] = cluster_intel

    return intel


def _as_dict(container: dict[str, Any], key: str) -> dict[str, Any]:
    """Return ``container[key]`` when it is a dict, else empty dict (safe)."""
    value = container.get(key)
    return value if isinstance(value, dict) else {}


def _safe_int(value: Any) -> int | None:
    """Best-effort int conversion (N/A-safe)."""
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    """Best-effort float conversion (N/A-safe)."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def render_analyst_read_block(data: dict[str, Any]) -> str:
    """Render the "🧠 Analyst Read" block over a deep-scan data dict.

    Builds the compact bundle/cluster intel summary, asks the snappy LLM
    (``app.llm_config.chat_with_fallback(messages, "snappy")`` — free
    OpenRouter tier with cheap paid fallback) for 2-3 plain-English sentences
    interpreting WHY the cluster structure matters, and returns an HTML-safe
    block. Fail-open: empty intel, timeout, rate-limit or blank answer all
    return "" so the caller skips the block entirely.
    """
    intel = _build_bundle_intel(data)
    if not intel:
        return ""
    import json

    from app.llm_config import chat_with_fallback

    messages: list[dict[str, str]] = [
        {"role": "system", "content": _ANALYST_READ_SYSTEM},
        {
            "role": "user",
            "content": (
                "Token bundle/cluster intel (JSON, only facts we measured):\n"
                f"{json.dumps(intel, default=str)[:800]}\n\n"
                "Write the Analyst Read now."
            ),
        },
    ]
    try:
        text = await asyncio.wait_for(chat_with_fallback(messages, "snappy"), timeout=_ANALYST_READ_TIMEOUT)
    except Exception as e:
        logger.warning("analyst read generation failed: %s", e)
        text = ""
    text = (text or "").strip()
    if not text:
        return ""
    return f"\U0001f9e0 <b>Analyst Read</b>\n{html.escape(text)[:_ANALYST_READ_MAX_CHARS]}"


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


def _sentinel_watch_line(red_flags: list[Any], level: str) -> str:
    """Short actionable "what to watch" line for the SENTINEL report."""
    joined = " ".join(str(f).lower() for f in red_flags)
    if "honeypot" in joined:
        return "Verify you can actually SELL before buying \u2014 honeypot signal."
    if "bundle" in joined:
        return "Volume & concentration may be fabricated \u2014 bundle activity."
    if "wash" in joined:
        return "Volume may be fake \u2014 wash trading suspected."
    lvl = str(level or "").lower()
    if lvl in ("critical", "high"):
        return "Avoid this token \u2014 critical risk signals detected."
    if lvl in ("medium", "moderate"):
        return "Proceed with extreme caution \u2014 elevated risk."
    return "Low risk \u2014 standard DeFi caution still applies."


def format_sentinel_deepscan_section(data: dict[str, Any], analyst_read: str = "") -> str:
    """Render the SENTINEL deep-forensic section.

    Consumes the raw response from ``POST /api/v1/x402-tools/sentinel_scan``
    (SentinelReport serialized): composite risk score, per-module risk
    breakdown, red flags, and per-module detail (holder HHI, LP lock,
    contract authority, deployer reputation). Used by /deepscan (appended to
    the base v2 scan report) and /sentinelscan (standalone).
    """
    s = sep()
    d = thin_sep()
    address = data.get("address") or data.get("token_address") or ""
    chain = data.get("chain") or "unknown"
    try:
        risk = float(data.get("composite_risk_score") or 0)
    except (TypeError, ValueError):
        risk = 0.0
    level = str(data.get("risk_level") or "unknown")
    red_flags = data.get("red_flags") or []
    module_scores = data.get("module_risk_scores") or {}
    holder = data.get("holder_analysis") or {}
    liq = data.get("liquidity_verification") or {}
    dev = data.get("dev_reputation") or {}
    authority = data.get("contract_authority") or {}
    bundle = data.get("bundle_detection") or {}
    exchange = data.get("exchange_funding") or {}

    lines: list[str] = [
        "\U0001f6e1\ufe0f <b>AEGIS DEEP FORENSIC SCAN</b>",
        s,
    ]
    if address:
        lines.append(f"<code>{address}</code>")
    lines.append(f"{fmt_chain(chain)}")
    # ── 1. One-line verdict up top (canonical layout) ──
    lines.append(
        f"{_risk_level_emoji(level)} <b>Verdict: {html.escape(level.upper())}</b> \u2014 Composite {risk:.0f}/100"
    )
    lines.append("")

    # ── AI Analyst Read (bundle/cluster plain-language interpretation) ──
    # Renders BEFORE the deterministic sections. Fail-open: an empty/absent
    # block (LLM down or no bundle data) is skipped entirely.
    if analyst_read:
        lines.extend(["", analyst_read])

    # ── Risk Assessment ──
    lines.append("\U0001f3af <b>Risk Assessment</b>")
    lines.append(d)
    lines.append(f"{_risk_level_emoji(level)} Composite Risk: <b>{risk:.0f}/100</b> ({level.upper()})")
    lines.append("")

    # ── Consolidated risk signals (scan_enrich output, when present) ──
    risk_lines = _format_risk_signals_section(data)
    if risk_lines:
        lines.extend(risk_lines)
        lines.append("")

    # ── Key findings (red flags) ──
    if red_flags:
        lines.append("\U0001f6a8 <b>Key Findings</b>")
        lines.append(d)
        for flag in red_flags[:8]:
            lines.append(f"  \U0001f534 {_clip(flag, 160)}")
        lines.append("")
    else:
        lines.append("\U0001f7e2 <b>No critical findings</b>")
        lines.append("")

    # ── Module risk breakdown ──
    if module_scores:
        lines.append("\U0001f4ca <b>Module Risk Breakdown</b>")
        lines.append(d)
        ranked = sorted(
            module_scores.items(),
            key=lambda kv: float(kv[1] or 0),
            reverse=True,
        )
        for mod, score in ranked[:10]:
            try:
                mscore = float(score or 0)
            except (TypeError, ValueError):
                mscore = 0.0
            memoji = SENTINEL_MODULE_EMOJI.get(mod, "\U0001f539")  # 🔹
            filled = max(0, min(10, int(mscore / 10)))
            mbar = "\u2b1b" * filled + "\u2b1c" * (10 - filled)
            lines.append(f"  {memoji} {mod.replace('_', ' ').title()}: {mscore:.0f}/100")
            lines.append(f"     <code>{mbar}</code>")
        lines.append("")

    # ── Holder analysis ──
    if holder:
        lines.append("\U0001f465 <b>Holder Analysis</b>")
        lines.append(d)
        hhi = holder.get("hhi")
        if hhi is not None:
            try:
                hhi_f = float(hhi)
            except (TypeError, ValueError):
                hhi_f = 0.0
            hhi_emoji = (
                "\U0001f534" if str(holder.get("risk_level") or "").lower() in ("high", "critical") else "\U0001f7e2"
            )
            lines.append(f"  {hhi_emoji} HHI Concentration: <b>{hhi_f:.0f}</b> ({holder.get('risk_level', 'unknown')})")
        if holder.get("total_holders") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f465 Total Holders: <b>{int(holder['total_holders']):,}</b>")
        if holder.get("top_10_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f40b Top-10 Supply: <b>{float(holder['top_10_percentage']):.0f}%</b>")
        if holder.get("top_20_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f40b Top-20 Supply: <b>{float(holder['top_20_percentage']):.0f}%</b>")
        if holder.get("exchange_funded_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f3e6 Exchange-Funded: <b>{float(holder['exchange_funded_percentage']):.0f}%</b>")
        if holder.get("new_wallets_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f195 New Wallets (<7d): <b>{float(holder['new_wallets_percentage']):.0f}%</b>")
        # One-line distribution verdict (concentrated vs healthy).
        top10_raw = holder.get("top_10_percentage")
        hhi_raw = holder.get("hhi")
        dist_emoji: str | None = None
        dist_word: str | None = None
        if top10_raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                top10_f = float(top10_raw)
                if top10_f >= 50:
                    dist_emoji, dist_word = "\U0001f534", "CONCENTRATED"
                elif top10_f >= 30:
                    dist_emoji, dist_word = "\U0001f7e1", "MODERATE"
                else:
                    dist_emoji, dist_word = "\U0001f7e2", "HEALTHY"
        if dist_emoji is None and hhi_raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                hhi_f = float(hhi_raw)
                if hhi_f >= 3000:
                    dist_emoji, dist_word = "\U0001f534", "CONCENTRATED"
                elif hhi_f >= 1500:
                    dist_emoji, dist_word = "\U0001f7e1", "MODERATE"
                else:
                    dist_emoji, dist_word = "\U0001f7e2", "HEALTHY"
        if dist_emoji is not None and dist_word is not None:
            lines.append(f"  {dist_emoji} Distribution verdict: <b>{dist_word}</b>")
        for w in (holder.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── LP analysis ──
    if liq:
        lines.append("\U0001f510 <b>LP Analysis</b>")
        lines.append(d)
        locked = liq.get("total_locked_percentage")
        if locked is not None:
            try:
                locked_f = float(locked)
            except (TypeError, ValueError):
                locked_f = 0.0
            lock_emoji = "\U0001f7e2" if locked_f >= 80 else "\U0001f7e1" if locked_f > 0 else "\U0001f534"
            lines.append(f"  {lock_emoji} LP Locked: <b>{locked_f:.0f}%</b>")
        if liq.get("lp_burned"):
            lines.append("  \U0001f525 LP tokens burned")
        if liq.get("fake_lock_detected"):
            lines.append("  \U0001f534 Fake lock detected")
        for w in (liq.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Contract authority ──
    if authority:
        lines.append("\U0001f512 <b>Contract Authority</b>")
        lines.append(d)
        ca_level = str(authority.get("risk_level") or "LOW").upper()
        lines.append(f"  {_risk_level_emoji(ca_level)} Authority Risk: <b>{ca_level}</b>")
        for w in (authority.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Bundle detection (SENTINEL bundle_detection module) ──
    if bundle:
        lines.append("\U0001f4e6 <b>Bundle Detection</b>")
        lines.append(d)
        bundle_detected = bool(
            bundle.get("deployer_funded")
            or bundle.get("same_block_snipers")
            or int(bundle.get("jito_bundles_detected") or 0) > 0
            or str(bundle.get("risk_level") or "").upper() in ("HIGH", "CRITICAL")
        )
        if bundle_detected:
            deployer_wallets = bundle.get("deployer_funded") or []
            snipers = bundle.get("same_block_snipers") or []
            n_wallets = len(deployer_wallets) + len(snipers)
            try:
                bundled_pct = float(bundle.get("total_bundled_percentage") or 0)
            except (TypeError, ValueError):
                bundled_pct = 0.0
            b_level = str(bundle.get("risk_level") or "UNKNOWN").upper()
            lines.append(f"  \U0001f534 Bundle detected: <b>{n_wallets}</b> wallets \u00b7 {bundled_pct:.0f}% bundled")
            lines.append(f"     Risk: <b>{b_level}</b>")
            if snipers:
                lines.append(f"     \U0001f3af Same-block snipers: <b>{len(snipers)}</b>")
            if deployer_wallets:
                lines.append(f"     \U0001f468\u200d\U0001f4bb Deployer-funded wallets: <b>{len(deployer_wallets)}</b>")
            if int(bundle.get("jito_bundles_detected") or 0) > 0:
                lines.append(f"     \u26a1 Jito bundles: <b>{int(bundle['jito_bundles_detected'])}</b>")
        else:
            lines.append("  \U0001f7e2 No bundle detected")
        # Same-slot buy concentration (own_bundle_detect, Helius-backed on
        # Solana). Honest + fail-open: show the concentration when scanned,
        # otherwise a short user-facing note (with the own bundle wallet
        # count + confidence when the SENTINEL module itself found nothing).
        same_slot = bundle.get("same_slot")
        if isinstance(same_slot, dict):
            if same_slot.get("result") == "scanned":
                with contextlib.suppress(TypeError, ValueError):
                    n_slot = int(same_slot.get("same_slot_wallets") or 0)
                    sol_spent = float(same_slot.get("same_slot_sol_spent") or 0)
                    held_pct = float(same_slot.get("same_slot_held_pct") or 0)
                    slot_no = same_slot.get("same_slot")
                    slot_txt = f"slot {slot_no}" if slot_no is not None else "same slot"
                    lines.append(
                        f"     \u26a1 Same-slot: <b>{n_slot}</b> wallets \u00b7 "
                        f"{sol_spent:.2f} SOL \u00b7 <b>{held_pct:.1f}%</b> held ({slot_txt})"
                    )
            elif same_slot.get("note"):
                lines.append("     <i>\u26a0\ufe0f Same-slot detail unavailable \u2014 data source offline</i>")
                if not bundle_detected:
                    n_own = int(same_slot.get("bundle_wallets") or 0)
                    conf = float(same_slot.get("confidence") or 0)
                    if n_own or conf:
                        lines.append(
                            f"     \U0001f4e6 Own bundle result: <b>{n_own}</b> wallets \u00b7 conf <b>{conf:.0f}%</b>"
                        )
        for w in (bundle.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Exchange funding (SENTINEL exchange_funding module) ──
    if exchange:
        lines.append("\U0001f3e6 <b>Exchange Funding</b>")
        lines.append(d)
        try:
            cex_pct = float(exchange.get("cex_funded_percentage") or 0)
        except (TypeError, ValueError):
            cex_pct = 0.0
        try:
            inst_pct = float(exchange.get("instant_buy_percentage") or 0)
        except (TypeError, ValueError):
            inst_pct = 0.0
        cex_count = int(exchange.get("cex_funded_count") or 0)
        total = int(exchange.get("total_buyers") or 0)
        cex_emoji = "\U0001f534" if cex_pct > 50 else "\U0001f7e1" if cex_pct > 20 else "\U0001f7e2"
        lines.append(f"  {cex_emoji} CEX-funded: <b>{cex_pct:.0f}%</b> ({cex_count}/{total} buyers)")
        if inst_pct > 0:
            lines.append(f"  \u26a1 Instant-buy (fund→buy <60s): <b>{inst_pct:.0f}%</b>")
        breakdown = exchange.get("exchange_breakdown")
        if isinstance(breakdown, dict) and breakdown:
            top = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)[:4]
            lines.append("  \U0001f3e6 " + " \u00b7 ".join(f"{k}: {v}" for k, v in top))
        e_level = str(exchange.get("risk_level") or "UNKNOWN").upper()
        lines.append(f"  {_risk_level_emoji(e_level)} Funding verdict: <b>{e_level}</b>")
        for w in (exchange.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Contract Creation Analysis ──
    creation = data.get("contract_creation") or {}
    if creation:
        lines.append("\U0001f4be <b>Contract Creation Analysis</b>")
        lines.append(d)
        creator = creation.get("creator_address")
        if creator:
            lines.append(f"  \U0001f464 Creator: <code>{creator}</code>")
        block = creation.get("block_number")
        if block:
            lines.append(f"  \U0001f4c5 Block: <b>{block:,}</b>")
        tx_hash = creation.get("tx_hash")
        if tx_hash:
            lines.append(f"  \U0001f517 Tx: <code>{tx_hash}</code>")
        first_interactors = creation.get("first_interactors") or []
        if first_interactors:
            lines.append(f"  \U0001f9e0 First Interactors ({len(first_interactors)}):")
            for i, w in enumerate(first_interactors[:5], 1):
                addr = w.get("address", "")
                label = w.get("label", "")
                fresh = w.get("is_fresh", False)
                fresh_badge = " 🆕" if fresh else ""
                lines.append(f"    {i}. <code>{addr}</code>{fresh_badge} {label}")
        fresh_pct = creation.get("fresh_wallet_percentage")
        if fresh_pct is not None:
            try:
                fpct = float(fresh_pct)
            except (TypeError, ValueError):
                fpct = 0.0
            fresh_emoji = "\U0001f534" if fpct > 50 else "\U0001f7e1" if fpct > 20 else "\U0001f7e2"
            lines.append(f"  {fresh_emoji} Fresh Wallets at Launch: <b>{fpct:.0f}%</b>")
        deployer_funded_pct = creation.get("deployer_funded_percentage")
        if deployer_funded_pct is not None:
            try:
                dfpct = float(deployer_funded_pct)
            except (TypeError, ValueError):
                dfpct = 0.0
            df_emoji = "\U0001f534" if dfpct > 30 else "\U0001f7e1" if dfpct > 10 else "\U0001f7e2"
            lines.append(f"  {df_emoji} Deployer-Funded at Launch: <b>{dfpct:.0f}%</b>")
        lines.append("")

    # ── First-20 Wallet Map (competitor feature) ──
    wallet_map = data.get("wallet_map") or {}
    if wallet_map:
        wm_line = wallet_map.get("wallet_map_line") or "\u2014"
        lines.append("\U0001f5fa\ufe0f <b>First-20 Wallet Map</b>")
        lines.append(d)
        lines.append(f"  <code>{html.escape(str(wm_line))}</code>")
        # Buyer timing (own wallet_map engine): fresh-wallet % + recently-
        # funded % among the first-buyers. Honest + fail-open: each line is
        # rendered only when the engine measured it; both absent → "—" and
        # the two lines are omitted entirely (never a fabricated 0%).
        fresh_line = str(wallet_map.get("fresh_wallet_line") or "\u2014").strip()
        funded_line = str(wallet_map.get("recently_funded_line") or "\u2014").strip()
        if fresh_line not in ("", "\u2014") or funded_line not in ("", "\u2014"):
            lines.append("")
            if fresh_line not in ("", "\u2014"):
                lines.append(f"  {html.escape(fresh_line)}")
            if funded_line not in ("", "\u2014"):
                lines.append(f"  {html.escape(funded_line)}")
        # CEX funding breakdown (own wallet_map engine): which exchanges
        # funded the holders' wallets — "who's behind this". Honest + fail-
        # open: only rendered when the engine measured it (never a fabricated
        # percentage); both absent → "—" and the lines are omitted entirely.
        cex_line = str(wallet_map.get("cex_funding_line") or "\u2014").strip()
        dep_line = str(wallet_map.get("deployer_funding_line") or "\u2014").strip()
        if cex_line not in ("", "\u2014") or dep_line not in ("", "\u2014"):
            lines.append("")
            if cex_line not in ("", "\u2014"):
                lines.append(f"  {html.escape(cex_line)}")
            if dep_line not in ("", "\u2014"):
                lines.append(f"  {html.escape(dep_line)}")
        lines.append("")

    # ── 🕸️ Cluster Analysis (funding-source clusters + coordinated buys) ──
    cluster = data.get("cluster_analysis") or {}
    if cluster:
        lines.append("\U0001f578\ufe0f <b>Cluster Analysis</b>")
        lines.append(d)
        if str(cluster.get("result")) == "scanned":
            cline = cluster.get("cluster_line") or "\u2014"
            lines.append(f"  <code>{html.escape(str(cline))}</code>")
            # Richer 🔴 Bundle verdict (beyond BubbleMaps/DevsNightmare):
            # same-slot + 2-hop funder chain + holding/dumped + time nodes.
            bline = cluster.get("bundle_line")
            if bline and str(bline).strip() not in ("", "\u2014"):
                lines.append(f"  <code>{html.escape(str(bline))}</code>")
            try:
                coord_buyers = int(cluster.get("coordinated_buyers") or 0)
            except (TypeError, ValueError):
                coord_buyers = 0
            if coord_buyers:
                lines.append(f"  \u26a0\ufe0f Coordinated buys (same block + shared funder): <b>{coord_buyers}</b>")
            try:
                chain_buyers = int(cluster.get("funding_chain_buyers") or 0)
            except (TypeError, ValueError):
                chain_buyers = 0
            if chain_buyers:
                lines.append(f"  \U0001f517 Funding-chain wallets (funder = launch buyer): <b>{chain_buyers}</b>")
            # ── Deeper dimensions: 2-hop chain, supply grab, time nodes ──
            try:
                depth = int(cluster.get("funding_chain_depth") or 0)
            except (TypeError, ValueError):
                depth = 0
            if depth >= 2:
                try:
                    two_hop = int(cluster.get("two_hop_chain_buyers") or 0)
                except (TypeError, ValueError):
                    two_hop = 0
                lines.append(f"  \U0001f517 2-hop funding chain: <b>{two_hop}</b> wallets")
            supply_grab = cluster.get("supply_grab")
            if isinstance(supply_grab, dict) and str(supply_grab.get("status") or "none") in ("holding", "dumped"):
                with contextlib.suppress(TypeError, ValueError):
                    sg_status = str(supply_grab.get("status") or "none")
                    held_pct = float(supply_grab.get("held_pct") or 0)
                    dumped_pct = float(supply_grab.get("dumped_pct") or 0)
                    exited = int(supply_grab.get("exited_wallets") or 0)
                    sg_word = "HOLDING" if sg_status == "holding" else "DUMPED"
                    sg_line = f"  \U0001f4e6 Supply grab: <b>{sg_word}</b> \u2014 holding {held_pct:.1f}% (dumped {dumped_pct:.1f}%)"
                    if exited:
                        sg_line += f" \u00b7 {exited} exited"
                    lines.append(sg_line)
            try:
                time_nodes = int(cluster.get("time_nodes") or 0)
            except (TypeError, ValueError):
                time_nodes = 0
            if time_nodes:
                lines.append(f"  \u23f1\ufe0f Time nodes (funded same hour): <b>{time_nodes}</b>")
        else:
            # Honest + fail-open: "—" when the engine has no data. A
            # A user-facing note explains the data-source gap when present.
            cluster_note = cluster.get("note")
            if cluster_note:
                lines.append(f"  <i>{html.escape(str(cluster_note))}</i>")
            else:
                lines.append("  <code>\u2014</code>")
        # ── Cross-token recurring cluster (own cross_token_link engine) ──
        # The same syndicate wallets showing up in PRIOR token launches —
        # "serial launcher" signal competitors miss. Rendered when the engine
        # measured a recurrence regardless of the cluster-analysis verdict
        # (the recurring check runs on its own ClickHouse/KG data). Honest +
        # fail-open: omitted entirely when the line is "—".
        cross_token = data.get("cross_token_link") or {}
        recurring_line = str(cross_token.get("recurring_cluster_line") or "\u2014").strip()
        if recurring_line not in ("", "\u2014"):
            lines.append(f"  {html.escape(recurring_line)}")
        lines.append("")

    # ── 🫧 Decentralization + Bubble Map (BubbleMaps-style, own data) ──
    decentralization = data.get("decentralization") or {}
    if decentralization:
        lines.append("\U0001fae7 <b>Decentralization & Bubble Map</b>")
        lines.append(d)
        dec_line = decentralization.get("decentralization_line")
        bubble_line = decentralization.get("bubble_line")
        has_content = any(str(x or "").strip() not in ("", "\u2014") for x in (dec_line, bubble_line))
        if has_content:
            if str(dec_line or "").strip() not in ("", "\u2014"):
                lines.append(f"  <code>{html.escape(str(dec_line))}</code>")
            if str(bubble_line or "").strip() not in ("", "\u2014"):
                lines.append(f"  <code>{html.escape(str(bubble_line))}</code>")
        else:
            # Honest + fail-open: "—" when the engine has no data.
            lines.append("  <code>\u2014</code>")
        breakdown = decentralization.get("breakdown") or {}
        if breakdown:
            with contextlib.suppress(TypeError, ValueError):
                top10 = float(breakdown.get("top10_pct") or 0)
                top20 = float(breakdown.get("top20_pct") or 0)
                whale = float(breakdown.get("whale_share") or 0)
                lines.append(
                    f"  \U0001f40b Top-10: <b>{top10:.0f}%</b> | Top-20: <b>{top20:.0f}%</b> | "
                    f"Whales (>=5%): <b>{whale:.0f}%</b>"
                )
            if int(breakdown.get("distinct_funders") or 0) > 0:
                with contextlib.suppress(TypeError, ValueError):
                    df = int(breakdown.get("distinct_funders") or 0)
                    largest = int(breakdown.get("largest_cluster_size") or 0)
                    source = str(breakdown.get("cluster_source") or "clickhouse").title()
                    lines.append(
                        f"  \U0001f578\ufe0f Funding clusters: <b>{df}</b> ({source}) | Largest: <b>{largest}</b>"
                    )
            if int(breakdown.get("time_nodes") or 0) > 0:
                with contextlib.suppress(TypeError, ValueError):
                    tn = int(breakdown.get("time_nodes") or 0)
                    lines.append(f"  \u23f1\ufe0f Time nodes (funded same-time): <b>{tn}</b>")
        lines.append("")

    # ── Deployer reputation ──
    if dev:
        lines.append("\U0001f468\u200d\U0001f4bb <b>Deployer Reputation</b>")
        lines.append(d)
        rep_score = dev.get("reputation_score")
        rep_level = str(dev.get("reputation_level") or "unknown")
        if rep_score is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  {_risk_level_emoji(rep_level)} Reputation: <b>{int(rep_score)}/100</b> ({rep_level.title()})"
                )
        if dev.get("total_launches") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  \U0001f4e6 Launches: <b>{int(dev['total_launches'])}</b> | "
                    f"Rugged: <b>{int(dev.get('rugged_count') or 0)}</b>"
                )
        rug_rate = dev.get("rug_rate")
        if rug_rate is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f4a5 Rug Rate: <b>{float(rug_rate) * 100:.0f}%</b>")
        for w in (dev.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Deployer history timeline (competitor feature) ──
    deployer_timeline = data.get("deployer_timeline") or {}
    if deployer_timeline:
        tl_line = deployer_timeline.get("deployer_timeline_line")
        if tl_line and str(tl_line).strip() not in ("", "\u2014"):
            lines.append("\U0001f4c5 <b>Deployer History Timeline</b>")
            lines.append(d)
            lines.append(f"  {html.escape(str(tl_line))}")
            lines.append("")

    # ── Cross-token linkage (serial-launcher: deployer + funder) ──
    cross_token = data.get("cross_token_link") or {}
    deployer_link = str(cross_token.get("deployer_link_line") or "\u2014").strip()
    funder_link = str(cross_token.get("funder_link_line") or "\u2014").strip()
    if deployer_link not in ("", "\u2014") or funder_link not in ("", "\u2014"):
        lines.append("\U0001f517 <b>Cross-Token Linkage</b>")
        lines.append(d)
        if deployer_link not in ("", "\u2014"):
            lines.append(f"  {html.escape(deployer_link)}")
        if funder_link not in ("", "\u2014"):
            lines.append(f"  {html.escape(funder_link)}")
        lines.append("")

    # ── Recommendations ──
    lines.append("\U0001f4a1 <b>Recommendations</b>")
    lines.append(d)
    lines.extend(f"  {rec}" for rec in _sentinel_recommendations(risk, level, red_flags))
    lines.append("")
    # ── 4. What to watch (short actionable line) ──
    lines.append(f"\U0001f440 <b>Watch:</b> {_sentinel_watch_line(red_flags, level)}")
    lines.append("")
    lines.append(
        "\u26a0\ufe0f <i>Composite risk is 0-100 (higher = riskier). "
        "AEGIS data is forensic evidence \u2014 always DYOR.</i>"
    )
    _addr = data.get("address", data.get("token", ""))
    _chain = data.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return _collapse_blank_lines("\n".join(lines))


def format_sentinel_scan_report(data: dict[str, Any], analyst_read: str = "") -> str:
    """Standalone /sentinelscan report — SENTINEL deep scan only."""
    return format_sentinel_deepscan_section(data, analyst_read=analyst_read)
