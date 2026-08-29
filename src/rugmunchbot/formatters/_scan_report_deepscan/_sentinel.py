"""SENTINEL deep-forensic scan section (POST /api/v1/x402-tools/sentinel_scan)."""

from __future__ import annotations

import contextlib
import html
from typing import Any

from rugmunchbot.config import fmt_chain
from rugmunchbot.formatters._base import footer_links, sep, thin_sep
from rugmunchbot.formatters._scan_report_common import (
    _clip,
    _collapse_blank_lines,
    _first_buyers_rows,
)
from rugmunchbot.formatters._scan_report_risk import _format_risk_signals_section
from rugmunchbot.formatters._scan_report_riskreport import format_risk_report_block
from rugmunchbot.formatters._sentinel_helpers import (
    SENTINEL_MODULE_EMOJI,
    _risk_level_emoji,
    _sentinel_recommendations,
)


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

    # ── AI Analyst verdict (backend LLM synthesis, 2026-08-29 depth push) ──
    # The v2 deepscan endpoint attaches ``ai_verdict`` (scan_verdict_ai.py).
    # Fail-open: rendered only when a non-trivial string is present.
    ai_verdict = data.get("ai_verdict")
    if isinstance(ai_verdict, str) and len(ai_verdict) > 20:
        lines.extend(["", "\U0001f916 <b>AI Analyst</b>", d])
        for raw_line in ai_verdict.strip().splitlines():
            line = html.escape(_clip(raw_line.strip(), 380))
            if line:
                lines.append(f"  {line}")

    # ── Risk Assessment ──
    lines.append("\U0001f3af <b>Risk Assessment</b>")
    lines.append(d)
    lines.append(
        f"{_risk_level_emoji(level)} Composite Risk: <b>{risk:.0f}/100</b> ({level.upper()})"
    )
    lines.append("")

    # ── Structured Risk Report (30-factor breakdown, 2026-08-29 depth push) ──
    # Renders the canonical explainable RiskReport (deployer / holders /
    # first-buyer / fresh% / CEX-funded% / bundle / beyond-market cluster /
    # honeypot / liquidity + ~30 factors + rug/scam determination). Uses the
    # backend-provided ``risk_report`` dict when present, otherwise
    # synthesizes the same shape from the payload. Fail-open: skipped when
    # there is no factor data at all.
    rr_lines = format_risk_report_block(data, deep=True)
    if rr_lines:
        lines.extend(rr_lines)
        lines.append("")

    # ── On-chain ground truth (bot-native RPC resolution) ──
    # Mint/freeze authority + Token-2022 extension facts resolved directly
    # from Solana RPC — always-available even when every provider dropped
    # them. Renders only what was actually resolved.
    oc_raw = data.get("onchain")
    oc: dict[str, Any] = oc_raw if isinstance(oc_raw, dict) else {}
    mint_active = data.get("mintable")
    freeze_active = data.get("freezable")
    if oc or mint_active is not None or freeze_active is not None:
        lines.append("\u2699\ufe0f <b>On-Chain Facts</b>")
        lines.append(d)
        if mint_active is not None:
            state = "\U0001f534 ACTIVE" if mint_active else "\U0001f7e2 renounced"
            lines.append(f"  \U0001f964 Mint Authority: <b>{state}</b>")
        if freeze_active is not None:
            state = "\U0001f534 ACTIVE" if freeze_active else "\U0001f7e2 renounced"
            lines.append(f"  \U0001f976 Freeze Authority: <b>{state}</b>")
        program = oc.get("token_program")
        if program:
            label = "Token-2022" if program == "token-2022" else "SPL Token"
            lines.append(f"  \U0001f9ea Program: <b>{label}</b>")
        exts = data.get("token_extensions") or []
        if exts:
            pretty = ", ".join(str(e).replace("_", " ") for e in exts[:6])
            lines.append(f"  \U0001f9ec Extensions: <b>{html.escape(pretty)}</b>")
        if oc.get("top_10_pct") is not None and not holder:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  \U0001f40b Top-10 hold <b>{float(oc['top_10_pct']):.1f}%</b>"
                    f" · Top-20 <b>{float(oc.get('top_20_pct') or 0):.1f}%</b> (RPC)"
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

    # ── 🛒 First Buyers (own indexer cohort, 2026-08-29 depth push) ──
    # The deep endpoint samples the top-250 earliest buyers (25 on quick
    # scans) with wallet-age buckets + bundling flags. Rendered only when the
    # engine produced a cohort — never a fabricated list. The aggregates line
    # shows the cohort depth explicitly (``first-250 cohort``).
    fb_rows = _first_buyers_rows(data, deep=True)
    if fb_rows:
        lines.append("\U0001f6d2 <b>First Buyers</b>")
        lines.append(d)
        lines.extend(fb_rows)
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
        if holder.get("top_20_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  \U0001f40b Top-20 Supply: <b>{float(holder['top_20_percentage']):.0f}%</b>"
                )
        if holder.get("exchange_funded_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  \U0001f3e6 Exchange-Funded: <b>{float(holder['exchange_funded_percentage']):.0f}%</b>"
                )
        if holder.get("new_wallets_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  \U0001f195 New Wallets (<7d): <b>{float(holder['new_wallets_percentage']):.0f}%</b>"
                )
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
            lock_emoji = (
                "\U0001f7e2" if locked_f >= 80 else "\U0001f7e1" if locked_f > 0 else "\U0001f534"
            )
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
    # v2 payload carries the own-data ``bundle_analysis`` block (detect_
    # bundled_launch: is_bundled / launch_cluster_score / deployer_funded_pct
    # / same_slot); the legacy SENTINEL module used ``bundle_detection``.
    # Both shapes render through the same section.
    bundle = data.get("bundle_detection") or data.get("bundle_analysis") or {}
    if bundle:
        lines.append("\U0001f4e6 <b>Bundle Detection</b>")
        lines.append(d)
        bundle_detected = bool(
            bundle.get("deployer_funded")
            or bundle.get("same_block_snipers")
            or int(bundle.get("jito_bundles_detected") or 0) > 0
            or str(bundle.get("risk_level") or "").upper() in ("HIGH", "CRITICAL")
            or bundle.get("is_bundled") is True
            or (bundle.get("launch_cluster_score") or 0) >= 70
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
            if n_wallets:
                lines.append(
                    f"  \U0001f534 Bundle detected: <b>{n_wallets}</b> wallets \u00b7 {bundled_pct:.0f}% bundled"
                )
            elif isinstance(bundle.get("launch_cluster_score"), (int, float)):
                # v2 own-data shape (bundle_analysis): no wallet lists, but a
                # definite verdict + launch cluster score carry the signal.
                lines.append(
                    "  \U0001f534 Bundle detected: "
                    f"<b>{str(bundle.get('is_bundled')).upper()}</b> "
                    f"(launch cluster {bundle['launch_cluster_score']:.0f}/100)"
                )
            else:
                lines.append("  \U0001f534 Bundle detected: <b>YES</b>")
            if b_level != "UNKNOWN":
                lines.append(f"     Risk: <b>{b_level}</b>")
            if snipers:
                lines.append(f"     \U0001f3af Same-block snipers: <b>{len(snipers)}</b>")
            if deployer_wallets:
                lines.append(
                    f"     \U0001f468\u200d\U0001f4bb Deployer-funded wallets: <b>{len(deployer_wallets)}</b>"
                )
            if int(bundle.get("jito_bundles_detected") or 0) > 0:
                lines.append(
                    f"     \u26a1 Jito bundles: <b>{int(bundle['jito_bundles_detected'])}</b>"
                )
            # Own-data bundle_analysis shape (v2 deepscan): cluster score +
            # deployer-funded % at launch + confidence.
            try:
                lcs = float(bundle.get("launch_cluster_score") or 0)
            except (TypeError, ValueError):
                lcs = 0.0
            if lcs:
                lines.append(f"     \U0001f3af Launch cluster score: <b>{lcs:.0f}/100</b>")
            try:
                dfp = float(bundle.get("deployer_funded_pct") or 0)
            except (TypeError, ValueError):
                dfp = 0.0
            if dfp:
                lines.append(
                    f"     \U0001f468\u200d\U0001f4bb Deployer-funded at launch: <b>{dfp:.0f}%</b>"
                )
            try:
                ftb = float(bundle.get("first_time_buyer_pct") or 0)
            except (TypeError, ValueError):
                ftb = 0.0
            if ftb:
                lines.append(f"     \U0001f195 First-time buyers: <b>{ftb:.0f}%</b>")
            try:
                conf = float(bundle.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0.0
            if conf:
                lines.append(f"     \U0001f9e0 Confidence: <b>{conf:.0f}%</b>")
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
                lines.append(
                    "     <i>\u26a0\ufe0f Same-slot detail unavailable \u2014 data source offline</i>"
                )
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
    # v2 deepscan carries the own-data ``wallet_metrics`` block
    # (compute_wallet_metrics: cex_funded_pct / fresh_wallet_pct /
    # first_tx_pct) — mapped onto the same section so the CEX-funded % always
    # renders when the engine measured it.
    exchange = data.get("exchange_funding") or {}
    if not exchange:
        _wm = data.get("wallet_metrics")
        if isinstance(_wm, dict) and _wm.get("cex_funded_pct") is not None:
            exchange = {
                "cex_funded_percentage": _wm.get("cex_funded_pct"),
                "instant_buy_percentage": _wm.get("first_tx_pct"),
                "total_buyers": _wm.get("holders_analyzed"),
            }
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
        # Only render the buyer-count suffix when the engine actually counted
        # CEX-funded wallets (wallet_metrics fallback has no count — a
        # fabricated 0/N would mislead).
        count_txt = (
            f" ({cex_count}/{total} buyers)" if exchange.get("cex_funded_count") is not None else ""
        )
        lines.append(f"  {cex_emoji} CEX-funded: <b>{cex_pct:.0f}%</b>{count_txt}")
        if inst_pct > 0:
            lines.append(f"  \u26a1 Instant-buy (fund→buy <60s): <b>{inst_pct:.0f}%</b>")
        breakdown = exchange.get("exchange_breakdown")
        if isinstance(breakdown, dict) and breakdown:
            top = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)[:4]
            lines.append("  \U0001f3e6 " + " \u00b7 ".join(f"{k}: {v}" for k, v in top))
        e_level = str(exchange.get("risk_level") or "UNKNOWN").upper()
        # wallet_metrics fallback carries no risk_level — derive a verdict
        # from the measured CEX share so the line never reads "UNKNOWN".
        if e_level == "UNKNOWN":
            e_level = "HIGH" if cex_pct > 50 else "MEDIUM" if cex_pct > 20 else "LOW"
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
    # v2 deepscan fallback: the structured RiskReport's beyond-market
    # wallet-cluster graph (8-leg risk_cluster engine — funder tree,
    # shared-deployer network, cross-token clusters/funders, temporal
    # lockstep, sniper/bot patterns, MEV bundles, wash cycles). Renders when
    # the legacy cluster_analysis module did not run but the engine did.
    cluster_is_rr_fallback = False
    if not cluster and isinstance(data.get("risk_report"), dict):
        _rr_cluster = (data["risk_report"] or {}).get("cluster")
        if isinstance(_rr_cluster, dict) and _rr_cluster.get("cluster_risk_score") is not None:
            cluster = {"result": "scanned", "cluster_risk_score": _rr_cluster}
            cluster_is_rr_fallback = True
    if cluster:
        lines.append("\U0001f578\ufe0f <b>Cluster Analysis</b>")
        lines.append(d)
        if str(cluster.get("result")) == "scanned":
            cline = cluster.get("cluster_line") or "\u2014"
            # The risk_report fallback has no cluster_line — skip the bare
            # placeholder so the real structured data below leads.
            if not cluster_is_rr_fallback or str(cline).strip() != "\u2014":
                lines.append(f"  <code>{html.escape(str(cline))}</code>")
            # Richer 🔴 Bundle verdict (beyond BubbleMaps/DevsNightmare):
            # same-slot + 2-hop funder chain + holding/dumped + time nodes.
            bline = cluster.get("bundle_line")
            if bline and str(bline).strip() not in ("", "\u2014"):
                lines.append(f"  <code>{html.escape(str(bline))}</code>")
            # Structured 8-leg risk_cluster score (v2 deepscan): the
            # consolidated 0-100 cluster risk from the beyond-market graph.
            _rc = cluster.get("cluster_risk_score")
            if isinstance(_rc, dict):
                _rc_score = _rc.get("cluster_risk_score")
                if _rc_score is not None:
                    with contextlib.suppress(TypeError, ValueError):
                        _rc_score_f = float(_rc_score)
                        _rc_label = str(_rc.get("cluster_risk_label") or "unknown").upper()
                        lines.append(
                            f"  \U0001f50e Cluster risk: <b>{_rc_score_f:.0f}/100</b>"
                            f" ({html.escape(_rc_label)})"
                        )
                        _rc_tree = _rc.get("funder_tree")
                        if isinstance(_rc_tree, list) and _rc_tree:
                            lines.append(
                                f"  \U0001f333 Funder tree: <b>{len(_rc_tree)}</b> root funder(s)"
                            )
                        _rc_x = _rc.get("cross_token_clusters")
                        if isinstance(_rc_x, list) and _rc_x:
                            lines.append(
                                f"  \U0001f517 Cross-token clusters: <b>{len(_rc_x)}</b> (syndicate recurrences)"
                            )
                        _rc_sn = _rc.get("sniper_patterns")
                        if isinstance(_rc_sn, list) and _rc_sn:
                            lines.append(f"  \U0001f3af Sniper/bot patterns: <b>{len(_rc_sn)}</b>")
                        _rc_mev = _rc.get("mev_bundles")
                        if isinstance(_rc_mev, list) and _rc_mev:
                            lines.append(f"  \u26a1 MEV bundles: <b>{len(_rc_mev)}</b>")
                        _rc_wash = _rc.get("wash_trading")
                        if isinstance(_rc_wash, dict) and (
                            _rc_wash.get("cycles") or _rc_wash.get("self_swaps")
                        ):
                            lines.append(
                                f"  \U0001f4b8 Wash cycles: <b>{len(_rc_wash.get('cycles') or [])}</b>"
                            )
            try:
                coord_buyers = int(cluster.get("coordinated_buyers") or 0)
            except (TypeError, ValueError):
                coord_buyers = 0
            if coord_buyers:
                lines.append(
                    f"  \u26a0\ufe0f Coordinated buys (same block + shared funder): <b>{coord_buyers}</b>"
                )
            try:
                chain_buyers = int(cluster.get("funding_chain_buyers") or 0)
            except (TypeError, ValueError):
                chain_buyers = 0
            if chain_buyers:
                lines.append(
                    f"  \U0001f517 Funding-chain wallets (funder = launch buyer): <b>{chain_buyers}</b>"
                )
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
            if isinstance(supply_grab, dict) and str(supply_grab.get("status") or "none") in (
                "holding",
                "dumped",
            ):
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
        has_content = any(
            str(x or "").strip() not in ("", "\u2014") for x in (dec_line, bubble_line)
        )
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
