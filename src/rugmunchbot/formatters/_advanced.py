"""Advanced / scan-report / news formatting blocks for the RugMunch bot.

Extracted from ``formatters.py`` during the Batch 11 de-godding pass:
Advanced flag rendering, deep-forensic scan report, own-data intel helpers,
the main ``/scan`` report and the news cards. ``formatters.py`` re-exports
every public name.
"""

from __future__ import annotations

import contextlib
import html
from datetime import UTC, datetime
from typing import Any

from rugmunchbot.config import (
    _risk_gauge,
    fmt_chain,
    fmt_pct,
    risk_bar,
    threat_indicator,
)
from rugmunchbot.formatters._base import footer_links, sep, thin_sep

# The parallel formatter refactor may add a dedicated verdict-header helper to
# _base.py. Import it defensively so this module works with or without it.
format_verdict_header: Any | None = None
with contextlib.suppress(
    ImportError
):  # pragma: no cover - exercised only when the _base helper is absent
    from rugmunchbot.formatters._base import (
        format_verdict_header,  # defensive optional import
    )

# The consolidated rug-risk scorer's one-line verdict renderer (pure, no I/O).
# Imported defensively so the Risk Signals section still degrades gracefully
# when the scorer module is not deployable.
_risk_verdict_text: Any | None = None
with contextlib.suppress(
    ImportError
):  # pragma: no cover - exercised only when the scorer helper is absent
    from rugmunchbot.integrations.rug_risk_score import (
        risk_verdict_text as _risk_verdict_text,  # defensive optional import
    )

from rugmunchbot.formatters._advanced_helpers import (  # noqa: E402  # optional imports above are intentional
    _ADVANCED_SCAN_CODE_MAP,  # noqa: F401  # re-exported for formatters/__init__
    ADVANCED_SCAN_MODULE_EMOJI,
    _advanced_free,
    _advanced_recommendations,
    _bluechip_addresses,  # noqa: F401  # re-exported for formatters/__init__
    _bundle_alert_line,
    _decode_advanced_warning,
    _deployer_history_line,
    _fmt_usd_amount,
    _module_warnings,
    _risk_level_emoji,
    _wallet_freshness_line,
    is_bluechip,
)

# --- Advanced deep forensic flags (v2 /scan + deep scan) ---


def format_advanced_flags(r: dict[str, Any]) -> str:
    """Render Advanced free-tier flags for the /scan report.

    Reads the v2 scanner's ``sentinel`` dict (free/premium module buckets;
    backend-contract field name — still ``sentinel`` until rmi-backend renames).
    Blue-chip tokens (WETH, WBTC, ...) legitimately trigger concentration /
    low-lock signals — those warnings are suppressed and replaced by a single
    green confirmation line. Returns "" when the v2 response carried no
    advanced-scan data.
    """
    advanced = r.get("sentinel")
    if not isinstance(advanced, dict):
        return ""
    d = thin_sep()
    if is_bluechip(r.get("address", "")):
        return (
            "\n\U0001f6e1\ufe0f <b>Advanced</b>\n"
            f"{d}\n"
            "  \u2705 <b>Known blue-chip</b> \u2014 concentration & lock signals suppressed"
        )
    free = _advanced_free(advanced)
    flags: list[str] = []
    if free:
        # Holder HHI concentration
        ha = free.get("holder_analysis")
        if isinstance(ha, dict):
            hhi = ha.get("hhi")
            level = str(ha.get("risk_level") or "").lower()
            if hhi is not None:
                try:
                    hhi_f = float(hhi)
                except (TypeError, ValueError):
                    hhi_f = 0.0
                if level in ("high", "critical"):
                    flags.append(f"\u26a0\ufe0f High holder concentration (HHI: {hhi_f:.0f})")
                else:
                    flags.append(f"\U0001f465 Holder concentration (HHI: {hhi_f:.0f}, {level})")
        # LP lock
        lv = free.get("liquidity_verification")
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            try:
                locked_f = float(lv["total_locked_percentage"])
            except (TypeError, ValueError):
                locked_f = 0.0
            flags.append(f"\U0001f510 LP Lock: {locked_f:.0f}%")
        # Contract authority
        ca = free.get("contract_authority")
        if isinstance(ca, dict):
            ca_level = str(ca.get("risk_level") or "LOW").upper()
            if ca_level in ("HIGH", "CRITICAL"):
                flags.append(f"\U0001f512 Contract authority risk: {ca_level}")
        # Proxy
        px = free.get("proxy_detection")
        if isinstance(px, dict):
            if px.get("can_upgrade"):
                flags.append("\u26a0\ufe0f Upgradeable proxy (admin can change implementation)")
            elif px.get("is_proxy"):
                flags.append(f"\U0001f4e6 Proxy contract ({px.get('proxy_type') or 'unknown'})")
        # Deployer reputation
        dr = free.get("dev_reputation")
        if isinstance(dr, dict):
            rep = str(dr.get("reputation_level") or "").lower()
            if rep in ("suspicious", "dangerous"):
                score = dr.get("reputation_score")
                score_str = f"{int(score)}/100" if score is not None else "N/A"
                flags.append(f"\U0001f468\u200d\U0001f4bb Dev reputation: {rep} ({score_str})")
        # Remaining module warnings (deduped, capped)
        for w in _module_warnings(
            free,
            "holder_analysis",
            "liquidity_verification",
            "contract_authority",
            "proxy_detection",
            "metadata_fingerprint",
            "dev_reputation",
        ):
            if len(flags) >= 6:
                break
            flags.append(f"\u26a0\ufe0f {w[:100]}")
    if not flags:
        flags.append("\u2705 No advanced red flags")
    lines = ["", "\U0001f6e1\ufe0f <b>Advanced</b>", d]
    lines.extend(f"  {f}" for f in flags)
    return "\n".join(lines)


# --- Advanced deep scan report (from POST /api/v1/x402-tools/sentinel_scan) ---


def format_advanced_deepscan_section(data: dict[str, Any]) -> str:
    """Render the Advanced deep-forensic section.

    Consumes the raw response from ``POST /api/v1/x402-tools/sentinel_scan``
    (the backend endpoint path is still ``sentinel_scan`` until the
    rmi-backend rename lands — SentinelReport serialized): composite risk
    score, per-module risk breakdown, red flags, and per-module detail
    (holder HHI, LP lock, contract authority, deployer reputation). Used by
    /deepscan (appended to the base v2 scan report) and /advancedscan
    (standalone).
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

    lines: list[str] = [
        "\U0001f6e1\ufe0f <b>Advanced Deep Forensic Scan</b>",
        s,
    ]
    if address:
        lines.append(f"<code>{address}</code>")
    lines.append(f"{fmt_chain(chain)}\n")

    # ── Composite risk assessment ──
    lines.append("\U0001f3af <b>Risk Assessment</b>")
    lines.append(d)
    lines.append(
        f"{_risk_level_emoji(level)} Composite Risk: <b>{risk:.0f}/100</b> ({level.upper()})"
    )
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
            lines.append(f"  \U0001f534 {str(flag)[:160]}")
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
            memoji = ADVANCED_SCAN_MODULE_EMOJI.get(mod, "\U0001f539")  # 🔹
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
                "\U0001f534"
                if str(holder.get("risk_level") or "").lower() in ("high", "critical")
                else "\U0001f7e2"
            )
            lines.append(
                f"  {hhi_emoji} HHI Concentration: <b>{hhi_f:.0f}</b> ({holder.get('risk_level', 'unknown')})"
            )
        if holder.get("total_holders") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f465 Total Holders: <b>{int(holder['total_holders']):,}</b>")
        if holder.get("top_10_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  \U0001f40b Top-10 Supply: <b>{float(holder['top_10_percentage']):.0f}%</b>"
                )
        for w in (holder.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
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
            lock_emoji = (
                "\U0001f7e2" if locked_f >= 80 else "\U0001f7e1" if locked_f > 0 else "\U0001f534"
            )
            lines.append(f"  {lock_emoji} LP Locked: <b>{locked_f:.0f}%</b>")
        if liq.get("lp_burned"):
            lines.append("  \U0001f525 LP tokens burned")
        if liq.get("fake_lock_detected"):
            lines.append("  \U0001f534 Fake lock detected")
        for w in (liq.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── Contract authority ──
    if authority:
        lines.append("\U0001f512 <b>Contract Authority</b>")
        lines.append(d)
        ca_level = str(authority.get("risk_level") or "LOW").upper()
        lines.append(f"  {_risk_level_emoji(ca_level)} Authority Risk: <b>{ca_level}</b>")
        for w in (authority.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
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
            lines.append(f"  \u26a0\ufe0f {str(w)[:120]}")
        lines.append("")

    # ── Recommendations ──
    lines.append("\U0001f4a1 <b>Recommendations</b>")
    lines.append(d)
    lines.extend(f"  {rec}" for rec in _advanced_recommendations(risk, level, red_flags))
    lines.append("")
    lines.append(
        "\u26a0\ufe0f <i>Composite risk is 0-100 (higher = riskier). "
        "Advanced data is forensic evidence \u2014 always DYOR.</i>"
    )
    _addr = data.get("address", data.get("token", ""))
    _chain = data.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return "\n".join(lines)


def format_advanced_scan_report(data: dict[str, Any]) -> str:
    """Standalone /advancedscan report — Advanced deep scan only."""
    return format_advanced_deepscan_section(data)


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

    # Deployer address (always shown)
    if deployer_addr and deployer_addr != "unknown":
        lines.append(f"  \U0001f464 Address: <code>{deployer_addr}</code>")
    else:
        lines.append("  \U0001f464 Address: <i>unknown</i>")

    # Launchpad detection
    try:
        from rugmunchbot.integrations.launchpad_registry import detect_launchpad

        launchpad = detect_launchpad(deployer_addr, chain)
        if launchpad:
            lp_name = launchpad.get("name", "unknown")
            lp_type = launchpad.get("type", "")
            lines.append(f"  \U0001f680 Launchpad: <b>{lp_name}</b> ({lp_type})")
    except (ConnectionError, OSError, ImportError):
        pass

    # Reputation label
    if reputation_label and reputation_label != "UNKNOWN":
        emoji = (
            "🔴"
            if reputation_label in ("CRITICAL", "HIGH")
            else "🟡"
            if reputation_label == "MEDIUM"
            else "🟢"
        )
        lines.append(f"  {emoji} Reputation: <b>{reputation_label}</b>")

    # Reputation score (0-100, higher = more trusted)
    if reputation_score is not None:
        try:
            score = float(reputation_score)
            emoji = "🟢" if score >= 70 else "🟡" if score >= 40 else "🔴"
            lines.append(f"  {emoji} Trust Score: <b>{score:.0f}/100</b>")
        except (TypeError, ValueError):
            pass

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
                hpct = (
                    float(honeypot_pct) * 100 if float(honeypot_pct) <= 1.0 else float(honeypot_pct)
                )
                lines.append(f"  \U0001f52a Honeypot Rate: <b>{hpct:.0f}%</b>")
            except (TypeError, ValueError):
                pass
        if chains_used:
            lines.append(f"  \U0001f310 Chains: <b>{', '.join(chains_used)}</b>")
        if label:
            lines.append(f"  \U0001f3f7\ufe0f Label: <b>{label}</b>")

    # Deployment info
    deployed_at = r.get("deployed_at") or r.get("deployment_timestamp")
    if deployed_at:
        lines.append(f"  \U0001f4c5 Deployed: <b>{str(deployed_at)[:19]}</b>")

    deployment_tx = r.get("deployment_tx") or r.get("deployment_hash")
    if deployment_tx:
        lines.append(f"  \U0001f517 TX: <code>{str(deployment_tx)[:20]}...</code>")

    return lines


def format_wallet_composition_section(
    r: dict[str, Any], token_created_ts: str | None = None
) -> str:
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
                    lines.append(
                        f"\U0001f3e6 Exchange-funded (CEX, label-based): <b>{cex:.1f}%</b>"
                    )
                return "\n".join(lines)
        except (TypeError, ValueError):
            pass  # unparseable dates — fall through to the full block

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
        emoji = {
            "LOW": "\U0001f7e2",
            "MEDIUM": "\U0001f7e1",
            "HIGH": "\U0001f534",
            "CRITICAL": "\U0001f6a8",
        }.get(risk, "\u26aa")
        lines.append(f"{emoji} Funding risk: <b>{risk}</b>")
    if not lines:
        return ""
    return (
        "\U0001f45b <b>Wallet Composition</b>" + scope + "\n" + thin_sep() + "\n" + "\n".join(lines)
    )


# ── Advanced warning code → human-readable description map ──
# --- Format scan report (professional redesign) ---
def _short_addr(addr: str) -> str:
    """Compact ``0xAb12...34cd`` form for long wallet addresses."""
    addr = str(addr)
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"


def _format_gmgn_section(r: dict[str, Any]) -> list[str]:
    """Compact GMGN "Smart Money / Holders" section (empty when no data).

    Consumes the ``gmgn`` sub-dict attached by
    ``rugmunchbot.integrations.scanner_engines.gmgn_enrich.enrich_scan_with_gmgn``::

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
                direction = str(w.get("direction") or "MIXED")
                if not addr:
                    continue
                arrow = {"BUY": "\U0001f7e2", "SELL": "\U0001f534", "MIXED": "\U0001f7e1"}.get(
                    direction, "\u26aa"
                )
                parts.append(f"{arrow} {_short_addr(addr)} {direction}")
            if parts:
                lines.append(f"  \U0001f9e0 {' | '.join(parts)}")

    return lines


def _verdict_text(risk: float) -> str:
    """One-line plain-language verdict for the 0-100 risk score."""
    if risk >= 80:
        return "\U0001f534 <b>EXTREME</b> \u2014 avoid unless you fully understand the risk"
    if risk >= 60:
        return "\U0001f7e1 <b>HIGH RISK</b> \u2014 significant red flags, DYOR before buying"
    if risk >= 40:
        return "\U0001f7e0 <b>MODERATE</b> \u2014 some risk signals, proceed with caution"
    if risk >= 20:
        return "\U0001f7e2 <b>LOW RISK</b> \u2014 few red flags detected"
    return "\u2705 <b>SAFE</b> \u2014 no major red flags detected"


# ── Risk Signals (consolidated "why it's sus" enrichment block) ──────────

# Pre-rendered per-module signal strings, in the order they render.
_RISK_SIGNAL_KEYS: tuple[str, ...] = (
    "bundle_signal",
    "reputation_signal",
    "wash_signal",
    "whale_signal",
    "authority_signal",
    "crossref_signal",
)


def _format_risk_signals_section(r: dict[str, Any]) -> list[str]:
    """Render the consolidated "why it's sus" block (empty when clean).

    Consumes the ``risk_signals`` dict attached by
    ``rugmunchbot.integrations.scanner_engines.scan_enrich.enrich_scan``::

        {
            "bundled": bool | None, "deployer_repeat": bool | None,
            "wash_trading": bool | None, "whale_flow": bool | None,
            "mintable": bool | None, "freezable": bool | None,
            "lp_locked": bool | float | None,
            "known_scammer": bool | None, "kol_handle": str | None,
            "bundle_signal": str | None,     # pre-rendered per-module lines
            "reputation_signal": str | None,
            "wash_signal": str | None, "whale_signal": str | None,
            "authority_signal": str | None, "crossref_signal": str | None,
            "rug_risk_score": {"score", "verdict", "flags", "reasons"},
            "risk_verdict_text": str,        # one-line verdict (pre-rendered)
        }

    Renders the consolidated verdict line plus every fired signal string,
    indented two spaces under a bold header (matching the other /scan
    sections). Signal strings are HTML-escaped — they come from databus /
    provider payloads and must never inject markup. Returns an empty list
    when the scan carried no ``risk_signals`` or none fired, so the report
    simply omits the section.
    """
    rs = r.get("risk_signals")
    if not isinstance(rs, dict):
        return []
    fired = [str(rs[key]) for key in _RISK_SIGNAL_KEYS if rs.get(key)]
    if not fired:
        return []

    lines: list[str] = ["", "\U0001f50d <b>Risk Signals</b>", thin_sep()]
    verdict = rs.get("risk_verdict_text")
    if not verdict and _risk_verdict_text is not None:
        try:
            verdict = _risk_verdict_text(rs.get("rug_risk_score") or {})
        except Exception:
            verdict = None
    if verdict:
        lines.append(f"  {html.escape(str(verdict))}")
    lines.extend(f"  \u26a0\ufe0f {html.escape(str(s))}" for s in fired)
    return lines


def _token_image_url(r: dict[str, Any]) -> str:
    """Best-effort token image URL from a scan result (fail-open).

    Accepts any of the keys the scanner/backends may use for the token
    logo: ``image_uri`` (Pump.fun API), ``image_url`` (DexScreener /
    generic), ``logo_url`` (Birdeye / legacy). Returns the first
    http(s) value found, else "".
    """
    for key in ("image_uri", "image_url", "logo_url", "image"):
        val = r.get(key)
        if isinstance(val, str) and val.strip().startswith(("http://", "https://")):
            return val.strip()
    return ""


def _token_description(r: dict[str, Any]) -> str:
    """Best-effort token description from a scan result (fail-open).

    Prefers the top-level ``description`` key (populated by the scanner
    when available) and falls back to the Advanced metadata-fingerprint
    module's ``description_snippet`` (Pump.fun / DexScreener description,
    metadata_fingerprint.py).
    """
    desc = r.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc.strip()
    try:
        mf = (r.get("sentinel") or {}).get("free", {}).get("metadata_fingerprint")
        if isinstance(mf, dict):
            snippet = mf.get("description_snippet")
            if isinstance(snippet, str) and snippet.strip():
                return snippet.strip()
    except (AttributeError, TypeError):
        pass
    return ""


def format_scan_report(r: dict[str, Any]) -> str:
    s = sep()
    d = thin_sep()

    # ── Header identity bits ──
    age_str = ""
    if r.get("created_at"):
        try:
            created = datetime.fromtimestamp(r["created_at"] / 1000, tz=UTC)
            age = datetime.now(UTC) - created
            if age.days > 365:
                age_str = f" | Age: {age.days // 365}y {age.days % 365 // 30}mo"
            elif age.days > 0:
                age_str = f" | Age: {age.days}d"
            else:
                age_str = f" | Age: {age.seconds // 3600}h"
        except (ConnectionError, OSError, TimeoutError):
            pass

    chain_str = fmt_chain(r["chain"])
    try:
        risk = float(r.get("risk_score", 0) or 0)
    except (TypeError, ValueError):
        risk = 0.0

    # ── 1. Verdict header (brand + identity + big risk score + one-line verdict) ──
    # The brand banner and token identity always render here; the risk
    # headline + verdict sentence come from _base.format_verdict_header when
    # the parallel refactor provides it, otherwise from the inline equivalent.
    lines = [
        "\U0001f6e1\ufe0f <b>Rug Munch Token Scan</b>",
        s,
        f"<b>{r['name']}</b> ({r['symbol']}) {chain_str}{age_str}",
        f"<code>{r['address']}</code>",
    ]
    verdict_rendered = False
    if format_verdict_header is not None:
        try:
            vh = format_verdict_header(r)
        except Exception:
            vh = None
        if vh:
            vh_lines = vh.split("\n") if isinstance(vh, str) else [str(line) for line in vh]
            lines.extend(f"  {line}" for line in vh_lines)
            verdict_rendered = True
    if not verdict_rendered:
        lines.append(f"  {_risk_gauge(risk)} \u2014 <b>{risk:.0f}/100</b>")
        lines.append(f"  \U0001f3af {_verdict_text(risk)}")

    # Bundle alert stays prominent, right after the verdict.
    bundle_alert = _bundle_alert_line(r)
    if bundle_alert:
        lines.extend(["", bundle_alert])

    # ── 2. Security Assessment (risk bar + contract checks + taxes / LP lock) ──
    lines.extend(["", "\U0001f512 <b>Security Assessment</b>", d, risk_bar(risk)])
    checks = []
    if r.get("honeypot") is not None:
        checks.append(f"  {threat_indicator('Honeypot', r['honeypot'])}")
    if r.get("contract_verified") is not None:
        checks.append(f"  {threat_indicator('Verified', not r['contract_verified'])}")
    if r.get("mintable") is not None:
        checks.append(f"  {threat_indicator('Mintable', r['mintable'])}")
    if r.get("owner_renounced") is not None:
        checks.append(f"  {threat_indicator('Ownership Renounced', not r['owner_renounced'])}")
    if checks:
        lines.extend(checks)
    tax_lines = []
    if r.get("buy_tax") is not None and r["buy_tax"] > 0:
        tax_lines.append(f"  \U0001f4e5 Buy Tax: <b>{r['buy_tax'] * 100:.1f}%</b>")
    if r.get("sell_tax") is not None and r["sell_tax"] > 0:
        tax_lines.append(f"  \U0001f4e4 Sell Tax: <b>{r['sell_tax'] * 100:.1f}%</b>")
    if r.get("lp_locked") is not None:
        tax_lines.append(f"  \U0001f510 LP Locked: <b>{r['lp_locked'] * 100:.0f}%</b>")
    if tax_lines:
        lines.extend(["", *tax_lines])

    # ── 2b. Risk Signals (consolidated "why it's sus" enrichment) ──
    risk_lines = _format_risk_signals_section(r)
    if risk_lines:
        lines.extend(risk_lines)

    # ── 3. Market Data ──
    price = r.get("price", 0)
    mcap = r.get("mcap", 0)
    liquidity = r.get("liquidity_usd") or r.get("liquidity", 0)
    vol_24h = r.get("volume_24h", 0)
    lines.extend(
        [
            "",
            "\U0001f4ca <b>Market Data</b>",
            d,
            f"  \U0001f4b0 Price: <b>{_fmt_usd_amount(price)}</b>",
            f"  \U0001f4c8 Market Cap: <b>{_fmt_usd_amount(mcap)}</b>",
            f"  \U0001f4a7 Liquidity: <b>{_fmt_usd_amount(liquidity)}</b>",
            f"  \U0001f4ca 24h Volume: <b>{_fmt_usd_amount(vol_24h)}</b>",
            f"  \U0001f504 1h: {fmt_pct(r.get('price_change_1h'))} | 24h: {fmt_pct(r.get('price_change_24h'))} | 7d: {fmt_pct(r.get('price_change_7d'))}",
        ]
    )

    # ── 4. Advanced Forensics ──
    advanced_flags = _format_advanced_pro(r)
    if advanced_flags:
        lines.extend(["", "\U0001f6e1\ufe0f <b>Advanced Forensics</b>", d])
        lines.extend(advanced_flags)

    # ── 5. Top Holders ──
    if r.get("top_holders"):
        lines.extend(["", "\U0001f465 <b>Top Holders</b>", d])
        for i, h in enumerate(r["top_holders"][:5], 1):
            tag = f" [{h['tag']}]" if h.get("tag") else ""
            cf = " \U0001f4c4" if h.get("is_contract") else ""
            lines.append(f"  {i}. <code>{h['address']}</code> - {h['pct']:.1f}%{tag}{cf}")

    # ── 5b. Smart Money / Holders (GMGN enrichment) ──
    gmgn_lines = _format_gmgn_section(r)
    if gmgn_lines:
        lines.extend(gmgn_lines)

    # ── 6. Deployer (always shown; pulled out of Advanced Analysis) ──
    deployer_lines = _format_deployer_section(r)
    if deployer_lines:
        lines.extend(deployer_lines)

    # ── 7. Advanced Analysis (single consolidated section) ──
    adv: list[str] = []
    if r.get("bundle_detected") and not bundle_alert:
        adv.append("  \U0001f578\ufe0f Bundle activity detected")
    if r.get("volume_real_ratio") is not None:
        ratio = r["volume_real_ratio"]
        emoji = "\U0001f534" if ratio > 5 else "\U0001f7e1" if ratio > 2 else "\U0001f7e2"
        adv.append(f"  {emoji} Vol/MCap ratio: <b>{ratio:.2f}x</b>")
    dev_wallet = r.get("dev_wallet")
    deployer_addr = r.get("deployer_address") or r.get("dev_wallet") or ""
    if dev_wallet and dev_wallet != deployer_addr and not any("Dev:" in l for l in deployer_lines):
        adv.append(f"  \U0001f468\u200d\U0001f4bb Dev: <code>{dev_wallet}</code>")
    wallet_line = _wallet_freshness_line(r)
    if wallet_line:
        adv.append(f"  {wallet_line}")
    elif r.get("fresh_wallet_pct", 0) > 0:
        adv.append(f"  \U0001f195 Fresh wallets: <b>{r['fresh_wallet_pct']:.0f}%</b>")
    if adv:
        lines.extend(["", "\U0001f52c <b>Advanced Analysis</b>", d])
        lines.extend(adv)

    # ── 8. Socials / Description ──
    # ALWAYS renders a Socials block when the token has any socials or an
    # image URL (never hidden behind a "NO SOCIALS" placeholder). Lists
    # Twitter / Telegram / website links plus the token image URL, with a
    # one-line safety note — external links are attacker-controlled surface
    # and must be verified before clicking. "NO SOCIALS" only when genuinely
    # none (no links AND no image).
    socials = r.get("socials", {})
    if not isinstance(socials, dict):
        socials = {}
    image_url = _token_image_url(r)
    description = _token_description(r)

    soc_parts: list[str] = []
    for stype, surl in socials.items():
        if not isinstance(surl, str) or not surl.strip():
            continue
        surl = surl.strip()
        if stype == "twitter":
            soc_parts.append(f'<a href="{html.escape(surl, quote=True)}">\U0001d54f Twitter</a>')
        elif stype == "telegram":
            soc_parts.append(f'<a href="{html.escape(surl, quote=True)}">\U0001f4f1 Telegram</a>')
        elif stype == "website":
            soc_parts.append(f'<a href="{html.escape(surl, quote=True)}">\U0001f310 Website</a>')
        else:
            soc_parts.append(
                f'<a href="{html.escape(surl, quote=True)}">\U0001f517 {html.escape(str(stype).capitalize())}</a>'
            )
    if image_url:
        soc_parts.append(
            f'<a href="{html.escape(image_url, quote=True)}">\U0001f5bc\ufe0f Image</a>'
        )

    lines.append("")
    lines.append("\U0001f517 <b>Socials</b>")
    lines.append(d)
    if soc_parts:
        lines.append("  " + " | ".join(soc_parts))
        lines.append("  \u26a0\ufe0f external links may be malicious \u2014 verify before clicking")
    else:
        lines.append("  \u26a0\ufe0f NO SOCIALS")
    if description:
        lines.append(f"  \U0001f4dd <i>{html.escape(description[:300])}</i>")

    # ── 9. Footer (trade links) ──
    lines.extend(["", s, "\U0001f4b1 <b>Trade:</b>"])
    _addr = r.get("address", r.get("token", ""))
    _chain = r.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return "\n".join(lines)


def _format_advanced_pro(r: dict[str, Any]) -> list[str]:
    """Professional advanced flag rendering with decoded warning codes."""
    advanced = r.get("sentinel")
    if not isinstance(advanced, dict):
        return []
    if is_bluechip(r.get("address", "")):
        return ["  \u2705 <b>Known blue-chip</b> — concentration & lock signals suppressed"]

    free = _advanced_free(advanced)
    flags: list[str] = []

    # Holder concentration
    ha = free.get("holder_analysis")
    if isinstance(ha, dict):
        hhi = ha.get("hhi")
        level = str(ha.get("risk_level") or "").lower()
        if hhi is not None:
            try:
                hhi_f = float(hhi)
            except (TypeError, ValueError):
                hhi_f = 0.0
            emoji = "🔴" if level in ("high", "critical") else "🟡" if level == "medium" else "🟢"
            flags.append(f"{emoji} Holder concentration: HHI {hhi_f:.0f} ({level})")

    # LP lock
    lv = free.get("liquidity_verification")
    if isinstance(lv, dict):
        locked = lv.get("total_locked_percentage")
        if locked is not None:
            try:
                locked_f = float(locked)
            except (TypeError, ValueError):
                locked_f = 0.0
            emoji = "🔴" if locked_f < 20 else "🟡" if locked_f < 80 else "🟢"
            flags.append(f"{emoji} LP Locked: {locked_f:.0f}%")
        liq_usd = lv.get("liquidity_usd")
        if liq_usd:
            flags.append(f"  Liquidity USD: {_fmt_usd_amount(liq_usd)}")

    # Contract authority
    ca = free.get("contract_authority")
    if isinstance(ca, dict):
        ca_level = str(ca.get("risk_level") or "LOW").upper()
        if ca_level in ("HIGH", "CRITICAL"):
            flags.append(f"🔴 Contract authority risk: {ca_level}")
        elif ca_level == "MEDIUM":
            flags.append(f"🟡 Contract authority: {ca_level}")

    # Proxy
    px = free.get("proxy_detection")
    if isinstance(px, dict):
        if px.get("can_upgrade"):
            flags.append("⚠️ Upgradeable proxy (admin can change implementation)")
        elif px.get("is_proxy"):
            flags.append(f"📦 Proxy contract ({px.get('proxy_type') or 'unknown'})")

    # Dev reputation
    dr = free.get("dev_reputation")
    if isinstance(dr, dict):
        rep = str(dr.get("reputation_level") or "").lower()
        if rep in ("suspicious", "dangerous"):
            score = dr.get("reputation_score")
            score_str = f"{int(score)}/100" if score is not None else "N/A"
            flags.append(f"🔴 Dev reputation: {rep} ({score_str})")

    # Decode raw warning codes (CR01, RP05, etc.)
    for w in _module_warnings(
        free,
        "holder_analysis",
        "liquidity_verification",
        "contract_authority",
        "proxy_detection",
        "metadata_fingerprint",
        "dev_reputation",
    ):
        decoded = _decode_advanced_warning(w)
        if decoded and decoded not in flags:
            if len(flags) >= 8:
                break
            flags.append(f"⚠️ {decoded}")

    if not flags:
        flags.append("✅ No advanced red flags")

    return flags


def format_news_card(nc: dict[str, Any] | None) -> list[str]:
    """Format a Ground News-style news card into scan-report lines.

    Accepts the engine payload from ``get_token_news_card``:

    .. code-block:: text

        📰 News Sentiment: BEARISH 🔴 (72 trending)
        ────────────────────────
        ├─ CoinDesk: "Coldcard exploit..." [VERIFIED] [HIGH IMPACT]
        ├─ TheBlock: "Bitcoin wallets drained..." [REPORTED]
        └─ 3 sources corroborate · on-chain evidence ✓

    Returns an empty list when there is no usable data so callers can
    skip the block entirely.
    """
    if not isinstance(nc, dict):
        return []
    articles = nc.get("articles")
    if not isinstance(articles, list) or not articles:
        return []

    sentiment = str(nc.get("sentiment") or "NO_DATA").upper()
    emoji = {
        "BULLISH": "\U0001f7e2",
        "BEARISH": "\U0001f534",
        "NEUTRAL": "\U0001f7e1",
        "NO_DATA": "\u26aa",
    }.get(sentiment, "\u26aa")
    trending = int(nc.get("trending_score") or 0)

    lines = [
        "",
        f"\U0001f4f0 News Sentiment: <b>{sentiment}</b> {emoji} ({trending} trending)",
        thin_sep(),
    ]
    for art in articles[:5]:
        source = str(art.get("source") or "Unknown")
        raw_title = str(art.get("title") or "").strip()
        title = raw_title[:60] + ("..." if len(raw_title) > 60 else "")
        factuality = str(art.get("factuality") or "").upper()
        impact = str(art.get("market_impact") or "").upper()
        tags = []
        if factuality in ("VERIFIED", "REPORTED", "OPINION", "RUMOR"):
            tags.append(f"[{factuality}]")
        if impact:
            tags.append(f"[{impact} IMPACT]")
        line = f'\u251c\u2500 {source}: "{title}"'
        if tags:
            line += " " + " ".join(tags)
        lines.append(line)

    corr = nc.get("corroboration") or {}
    if isinstance(corr, dict):
        total = int(corr.get("total_sources") or 0)
        on_chain = bool(corr.get("on_chain_evidence"))
        if total >= 2 or on_chain:
            corr_line = f"\u2514\u2500 {total} sources corroborate"
            if on_chain:
                corr_line += " \u00b7 on-chain evidence \u2713"
            lines.append(corr_line)
    return lines


def format_news_sentiment(ns) -> list[str]:
    """Format a news_sentiment dict into scan-report lines (empty when absent).

    Accepts the engine payload from ``analyze_token_news_sentiment`` (or a
    dict with the same keys). Returns an empty list when there is no usable
    data so callers can skip the block entirely.
    """
    if not isinstance(ns, dict):
        return []
    if not ns.get("article_count"):
        return []
    sentiment = str(ns.get("sentiment") or "NO_DATA").upper()
    emoji = {
        "BULLISH": "\U0001f7e2",
        "BEARISH": "\U0001f534",
        "NEUTRAL": "\U0001f7e1",
        "NO_DATA": "\u26aa",
    }.get(sentiment, "\u26aa")
    lines = [
        "",
        "\U0001f4f0 <b>News Sentiment</b>",
        thin_sep(),
        f"{emoji} Sentiment: <b>{sentiment}</b> (confidence {float(ns.get('confidence') or 0) * 100:.0f}%)",
        f"\U0001f4f0 Articles: {int(ns.get('article_count') or 0)}",
    ]
    breakdown = ns.get("sentiment_breakdown") or {}
    if isinstance(breakdown, dict):
        lines.append(
            f"\U0001f4c8 Breakdown: \U0001f7e2{breakdown.get('positive', 0)} "
            f"\U0001f534{breakdown.get('negative', 0)} "
            f"\U0001f7e1{breakdown.get('neutral', 0)}"
        )
    if ns.get("trending_score") is not None:
        lines.append(f"\U0001f525 Media Attention: <b>{int(ns['trending_score'])}/100</b>")
    flags = ns.get("flags") or []
    if isinstance(flags, list) and flags:
        lines.append(f"\U0001f3c6 {', '.join(str(f) for f in flags[:3])}")
    return lines
