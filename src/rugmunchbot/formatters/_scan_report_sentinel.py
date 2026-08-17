"""SENTINEL (AEGIS) flag renderers for the scan report.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: the free-tier AEGIS flags block and the professional
sentinel renderer consumed by ``format_scan_report``."""

from __future__ import annotations

import html
from typing import Any

from app.domains.telegram.rugmunchbot.formatters._base import thin_sep
from app.domains.telegram.rugmunchbot.formatters._scan_report_common import _clip
from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import (
    _decode_sentinel_warning,
    _fmt_usd_amount,
    _module_warnings,
    _sentinel_free,
    is_bluechip,
)

__all__ = [
    "_format_sentinel_pro",
    "format_sentinel_flags",
]

# --- SENTINEL deep forensic flags (v2 /scan + deep scan) ---


def format_sentinel_flags(r: dict[str, Any]) -> str:
    """Render SENTINEL free-tier flags for the /scan report.

    Reads the v2 scanner's ``sentinel`` dict (free/premium module buckets).
    Blue-chip tokens (WETH, WBTC, ...) legitimately trigger concentration /
    low-lock signals — those warnings are suppressed and replaced by a single
    green confirmation line. Returns "" when the v2 response carried no
    sentinel data.
    """
    sentinel = r.get("sentinel")
    if not isinstance(sentinel, dict):
        return ""
    d = thin_sep()
    if is_bluechip(r.get("address", "")):
        return (
            "\n\U0001f6e1\ufe0f <b>AEGIS</b>\n"
            f"{d}\n"
            "  \u2705 <b>Known blue-chip</b> \u2014 concentration & lock signals suppressed"
        )
    free = _sentinel_free(sentinel)
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
                    flags.append(f"\u26a0\ufe0f High holder concentration (HHI: <b>{hhi_f:.0f}</b>)")
                else:
                    flags.append(f"\U0001f465 Holder concentration (HHI: <b>{hhi_f:.0f}</b>, {html.escape(level)})")
        # LP lock
        lv = free.get("liquidity_verification")
        if isinstance(lv, dict) and lv.get("total_locked_percentage") is not None:
            try:
                locked_f = float(lv["total_locked_percentage"])
            except (TypeError, ValueError):
                locked_f = 0.0
            flags.append(f"\U0001f510 LP Lock: <b>{locked_f:.0f}%</b>")
        # Contract authority
        ca = free.get("contract_authority")
        if isinstance(ca, dict):
            ca_level = html.escape(str(ca.get("risk_level") or "LOW")).upper()
            if ca_level in ("HIGH", "CRITICAL"):
                flags.append(f"\U0001f512 Contract authority risk: <b>{ca_level}</b>")
        # Proxy
        px = free.get("proxy_detection")
        if isinstance(px, dict):
            if px.get("can_upgrade"):
                flags.append("\u26a0\ufe0f Upgradeable proxy (admin can change implementation)")
            elif px.get("is_proxy"):
                flags.append(f"\U0001f4e6 Proxy contract ({html.escape(str(px.get('proxy_type') or 'unknown'))})")
        # Deployer reputation
        dr = free.get("dev_reputation")
        if isinstance(dr, dict):
            rep = str(dr.get("reputation_level") or "").lower()
            if rep in ("suspicious", "dangerous"):
                score = dr.get("reputation_score")
                score_str = f"{int(score)}/100" if score is not None else "N/A"
                flags.append(f"\U0001f468\u200d\U0001f4bb Dev reputation: <b>{html.escape(rep)}</b> ({score_str})")
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
            flags.append(f"\u26a0\ufe0f {html.escape(_clip(w, 100))}")
    if not flags:
        flags.append("\u2705 No AEGIS red flags")
    lines = ["", "\U0001f6e1\ufe0f <b>AEGIS</b>", d]
    lines.extend(f"  {f}" for f in flags)
    return "\n".join(lines)


def _format_sentinel_pro(r: dict[str, Any]) -> list[str]:
    """Professional sentinel flag rendering with decoded warning codes.

    Mirrors the /scan metric style: label plain, VALUE in ``<b>`` (HHI, LP
    %, risk levels, scores). Flags are emitted WITHOUT a leading indent —
    the caller indents them two spaces under the AEGIS header.
    """
    sentinel = r.get("sentinel")
    if not isinstance(sentinel, dict):
        return []
    if is_bluechip(r.get("address", "")):
        return ["  \u2705 <b>Known blue-chip</b> — concentration & lock signals suppressed"]

    free = _sentinel_free(sentinel)
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
            emoji = (
                "\U0001f534" if level in ("high", "critical") else "\U0001f7e0" if level == "medium" else "\U0001f7e2"
            )  # 🔴 / 🟡 / 🟢
            flags.append(f"{emoji} Holder concentration: HHI <b>{hhi_f:.0f}</b> ({html.escape(level)})")

    # LP lock
    lv = free.get("liquidity_verification")
    if isinstance(lv, dict):
        locked = lv.get("total_locked_percentage")
        if locked is not None:
            try:
                locked_f = float(locked)
            except (TypeError, ValueError):
                locked_f = 0.0
            emoji = "\U0001f534" if locked_f < 20 else "\U0001f7e1" if locked_f < 80 else "\U0001f7e2"
            flags.append(f"{emoji} LP Locked: <b>{locked_f:.0f}%</b>")
        liq_usd = lv.get("liquidity_usd")
        if liq_usd:
            flags.append(f"\U0001fa99 Liquidity USD: <b>{_fmt_usd_amount(liq_usd)}</b>")

    # Contract authority
    ca = free.get("contract_authority")
    if isinstance(ca, dict):
        ca_level = html.escape(str(ca.get("risk_level") or "LOW")).upper()
        if ca_level in ("HIGH", "CRITICAL"):
            flags.append(f"\U0001f534 Contract authority risk: <b>{ca_level}</b>")
        elif ca_level == "MEDIUM":
            flags.append(f"\U0001f7e1 Contract authority: <b>{ca_level}</b>")

    # Proxy
    px = free.get("proxy_detection")
    if isinstance(px, dict):
        if px.get("can_upgrade"):
            flags.append("\u26a0\ufe0f Upgradeable proxy (admin can change implementation)")
        elif px.get("is_proxy"):
            flags.append(f"\U0001f4e6 Proxy contract ({html.escape(str(px.get('proxy_type') or 'unknown'))})")

    # Dev reputation
    dr = free.get("dev_reputation")
    if isinstance(dr, dict):
        rep = str(dr.get("reputation_level") or "").lower()
        if rep in ("suspicious", "dangerous"):
            score = dr.get("reputation_score")
            score_str = f"{int(score)}/100" if score is not None else "N/A"
            flags.append(f"\U0001f534 Dev reputation: <b>{html.escape(rep)}</b> ({score_str})")

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
        decoded = _decode_sentinel_warning(w)
        if decoded and decoded not in flags:
            if len(flags) >= 8:
                break
            flags.append(f"\u26a0\ufe0f {html.escape(_clip(decoded, 140))}")

    if not flags:
        flags.append("\u2705 No AEGIS red flags")

    return flags
