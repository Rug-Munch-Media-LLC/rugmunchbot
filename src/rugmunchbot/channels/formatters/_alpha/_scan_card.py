"""Scan-card rendering: suspicious-token card posted by the scans channel.

Pure rendering only. Split out of ``_alpha.py`` (2026-10-23).
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from rugmunchbot.channels.formatters._alerts import _item_links, _scan_reason_bits
from rugmunchbot.channels.formatters._alpha._scan_signal import _scan_signal_lines
from rugmunchbot.channels.formatters._primitives import (
    NewsPost,
    _clip,
    _esc,
    _graphic_for,
    _is_placeholder,
    _now_stamp,
    _pick_image_url,
    _risk_emoji,
    _scan_channel_shill,
)
from rugmunchbot.formatters import sep
from rugmunchbot.formatters._scan_report_riskreport import format_risk_report_block


def format_scan_card(item: dict[str, Any]) -> NewsPost:
    """Engaging @rmiscans card with full address + bot instructions + cross-channel promo.

    One token per card (the scans job posts each notable scan as its own
    card, ~15-20/day). The header reads "Suspicious Tokens & Scans of Interest"
    and every card carries the FULL token address (never truncated), a clear
    call-to-action to /scan with @RugMunchBot, chart/rugmap links, and a
    compact cross-channel shill block so readers discover the full RugMunch
    ecosystem. The reasons line surfaces the ACTUAL why a token is risky —
    honeypot / bundle / fake volume / LP removed / flagged deployer
    (``_scan_reason_bits``) — instead of the generic "high-risk verdict";
    the optional ``commentary`` key renders the one-line AI wit when present
    (fail-open to no line). Name/symbol render honestly: no "Unknown (???)"
    when only one of them is known. The score line renders the numeric risk
    (or falls back to the risk level when the score is absent),
    and the scan deep link always points at rugmunch.io/scanner. Carries the
    item's image when one exists (text-only otherwise — fail-open).
    """
    raw_name = str(item.get("name") or "").strip()
    raw_symbol = str(item.get("symbol") or "").strip()
    name = "" if _is_placeholder(raw_name) else raw_name
    symbol = "" if _is_placeholder(raw_symbol) else raw_symbol
    if name and symbol and symbol.lower() != name.lower():
        label = f"{_esc(name)} ({_esc(symbol)})"
    elif symbol:
        label = _esc(symbol)
    elif name:
        label = _esc(name)
    else:
        label = "Unnamed token"
    addr = str(item.get("address") or item.get("token_address") or "")
    chain = _esc(item.get("chain") or "")
    risk = str(item.get("risk_level") or "unknown").lower()
    chain_part = f" \u00b7 {chain}" if chain else ""
    lines = [
        "\U0001f52c <b>Suspicious Tokens &amp; Scans of Interest</b>",
        "<i>Notable tokens flagged by the RugMunch scanner \u2014 always /scan before buying.</i>",
        sep(),
        "",
        f"{_risk_emoji(risk)} <b>{label}</b>{chain_part}",
    ]
    # FULL address — never truncated so users can copy-paste to /scan
    if addr:
        lines.append(f"<code>{_esc(addr)}</code>")
    lines.append("")
    lines.append(_score_line(item, risk))
    reasons = _scan_reason_bits(item)
    if reasons:
        lines.append("\u26a0\ufe0f " + " \u00b7 ".join(_esc(r) for r in reasons))
    # ── Scans-of-interest signal (2026-08-29 fix) ──
    # The REAL why a reader needs: holders count, liquidity amount, volume,
    # first-25-buyers, bundle/deployer flags — the actual signal, rendered
    # only when the scan reported it. A ``scan_pending`` card (deep phases
    # still running) says so honestly — "Scanning…" — and carries NO
    # RiskReport block (a thin 1/30-factor composite would contradict the
    # header). Never "Deep analysis unavailable".
    signal = _scan_signal_lines(item)
    if signal:
        lines.append("")
        lines.append("\U0001f9e0 " + " \u00b7 ".join(signal))
    if item.get("scan_pending"):
        lines.append("")
        lines.append(
            "\u23f3 <i>Scanning\u2026 deep analysis in progress \u2014 "
            "I\u2019ll repost the full report when it\u2019s ready.</i>"
        )
    elif addr:
        # Full-product RiskReport block (deployer / holders / first-buyer /
        # top-25 / fresh% / CEX% / bundle / cluster / honeypot / liquidity +
        # ~30-factor score + rug/scam determination) — the same RiskReport the
        # /scan formatter renders. Gated upstream: a report with too few known
        # factors to declare a verdict is omitted entirely rather than
        # rendering a composite that contradicts the card header.
        risk_report_lines = format_risk_report_block(item, deep=False)
        if risk_report_lines:
            lines.append("")
            lines.extend(risk_report_lines)
    commentary = str(item.get("commentary") or "").strip()
    if commentary:
        lines.append(f"\U0001f4ac \u201c{_esc(commentary)}\u201d")
    links = _item_links(item, addr=addr, chain=str(item.get("chain") or ""))
    if links:
        lines.append("")
        # Add bot scan command — this is the KEY hook to get users on @RugMunchBot
        scan_cmd = f"/scan <code>{_esc(addr)}</code>"
        lines.append(f"\U0001f916 <b>Scan with @RugMunchBot:</b> {scan_cmd}")
        lines.append(links)
    lines.extend(["", f"\U0001f550 {_now_stamp()}"])
    lines.append(sep())
    lines.append(_scan_channel_shill())
    post = NewsPost(_clip("\n".join(lines)))
    post.image_url = _pick_image_url(item.get("image_url")) or _graphic_for("scan")
    return post


def _score_line(item: dict[str, Any], risk: str) -> str:
    """Numeric risk line, falling back to the verdict when no score is present."""
    score = item.get("risk_score")
    if score is not None:
        with suppress(TypeError, ValueError):
            return (
                f"{_risk_emoji(risk)} <b>Risk Score: {float(score):.0f}/100</b> "
                f"\u2014 {risk.upper()}"
            )
    return f"{_risk_emoji(risk)} <b>Verdict: {risk.upper()}</b>"
