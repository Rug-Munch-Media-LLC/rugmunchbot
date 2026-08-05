#!/usr/bin/env python3
"""
Deep-scan profile formatters — WalletBreakdown / BundleProfile / DeployerProfile.

Renders the new deep-scan profile data as Telegram HTML (bold values, code
addresses, emoji risk color-coding). Follows the V3 formatting conventions from
services/formatting.py (esc/code/risk_tier) while staying defensive over unknown
field shapes — nested dicts, flat keys, and legacy aliases are all accepted.

Public API:
  - format_wallet_breakdown(wb: dict) -> str        # 📊 section body
  - format_bundle_profile(bp: dict) -> str          # 📦 section body
  - format_deployer_profile(dp: dict) -> str        # 👤 section body
  - format_deep_scan_summary(profile: dict) -> list[str]  # 🔬 combined report
  - format_deepscan_profile_sections(data: dict) -> list[str]  # locate + render

Design rules:
  - HTML tags: <b>bold</b>, <i>italic</i>, <code>mono</code>, <pre>blocks</pre>
  - Color-coded risk: 🟢 safe, 🟡 caution, 🔴 danger
  - Missing / zero / None fields are skipped gracefully
  - format_deep_scan_summary returns list[str] so callers can send multiple
    messages when the combined report exceeds Telegram's 4096-char limit.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.formatting import esc, thin_sep

__all__ = [
    "format_bundle_profile",
    "format_deep_scan_summary",
    "format_deepscan_profile_sections",
    "format_deployer_profile",
    "format_wallet_breakdown",
]

MAX_MESSAGE_LEN = 4096

_HEADER_SEP = "═" * 26

# Risk level → Telegram emoji (3-color scheme from the bot spec).
_RISK_EMOJI = {
    "critical": "🔴",
    "high": "🔴",
    "danger": "🔴",
    "severe": "🔴",
    "medium": "🟡",
    "caution": "🟡",
    "warning": "🟡",
    "low": "🟢",
    "safe": "🟢",
    "ok": "🟢",
}

_SUSPICIOUS_FUNDERS = (
    "tornado",
    "mixer",
    "sanctioned",
    "anonym",
    "privacy",
    "bridge",
    "eoa",
    "faucet",
)

_BOTLIKE_PATTERNS = ("bot", "auto", "script", "mechan", "cycle", "schedule", "interval")


def _num(value) -> str:
    """Thousands-separated integer: 1247 -> '1,247'. Returns '' for junk."""
    try:
        cleaned = str(value).replace(",", "").strip()
        return f"{int(float(cleaned)):,}"
    except (TypeError, ValueError):
        return ""


def _num_float(value, default: float = 0.0) -> float:
    """Numeric coercion for sorting/comparison — never raises."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(value) -> str:
    """One-decimal percentage: 71.5 -> '71.5%'. Returns '' for junk."""
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return ""


def _pct_frac(value) -> str:
    """Percentage that tolerates 0-1 fractions: 0.41 -> '41.0%', 71.5 -> '71.5%'."""
    v = _num_float(value)
    if 0 < v <= 1:
        v *= 100
    return f"{v:.1f}%"


def _pct_int(value) -> str:
    """Whole percentage: 62 -> '62%'. Returns '' for junk."""
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return ""


def _count_pct(count, pct) -> str:
    """'892 (71.5%)' — or just '892' when the percentage is missing/zero."""
    count_s = _num(count)
    if not count_s:
        return ""
    if not pct:
        return count_s
    return f"{count_s} ({_pct(pct)})"


def _num_pct(data: dict, base_key: str) -> tuple:
    """Resolve a (count, pct) pair from nested, flat, or alias shapes.

    Accepts:
      {"fresh_wallets": {"count": 892, "pct": 71.5}}
      {"fresh_wallets": 892, "fresh_wallet_pct": 71.5}
      {"fresh_wallets": {"total": 892, "percent": 71.5}}
      {"fresh_wallet_count": 892, "fresh_wallet_pct": 71.5}
    """
    raw = data.get(base_key)
    if isinstance(raw, dict):
        count = raw.get("count", raw.get("total", raw.get("n")))
        pct = raw.get("pct", raw.get("percent", raw.get("percentage")))
        return count, pct
    if raw is not None:
        count = raw
        pct = data.get(f"{base_key}_pct", data.get(f"{base_key}_percent"))
        return count, pct
    count = data.get(f"{base_key}_count", data.get(f"{base_key}_total"))
    pct = data.get(f"{base_key}_pct", data.get(f"{base_key}_percent"))
    return count, pct


def _elevated(value, red: float = 60, yellow: float = 40) -> str:
    """Risk emoji for a 0-100 metric: red/yellow only when elevated."""
    v = _num_float(value)
    if v >= red:
        return "🔴"
    if v >= yellow:
        return "🟡"
    return ""


def _risk_emoji(level_or_score) -> str:
    """Map a risk level string or 0-100 score to a 3-color emoji."""
    from services.formatting import risk_tier

    if isinstance(level_or_score, str):
        return _RISK_EMOJI.get(level_or_score.strip().lower(), "")
    score = _num_float(level_or_score)
    if score == 0 and level_or_score not in (0, 0.0):
        return ""
    return _RISK_EMOJI.get(risk_tier(score), "")


def _short(addr: str) -> str:
    """0x742d...eb48 style short form for long addresses."""
    if len(addr) > 12:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def _suspicious(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _SUSPICIOUS_FUNDERS)


def _botlike(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _BOTLIKE_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
# Section formatters (each returns a single message body as str)
# ─────────────────────────────────────────────────────────────────────────────


def format_wallet_breakdown(wb: dict) -> str:
    """📊 Wallet Breakdown — holder composition & diversity signals."""
    if not isinstance(wb, dict) or not wb:
        return ""
    lines = ["📊 <b>Wallet Breakdown</b>"]

    total = wb.get("total_holders") or wb.get("holders_total") or wb.get("holders")
    if total:
        lines.append(f"• Total holders: <b>{_num(total)}</b>")

    fw_count, fw_pct = _num_pct(wb, "fresh_wallets")
    if fw_count:
        emoji = _elevated(fw_pct)
        body = _count_pct(fw_count, fw_pct)
        lines.append(f"• Fresh wallets (&lt;7d): <b>{body}</b>{(' ' + emoji) if emoji else ''}")

    cex_count, cex_pct = _num_pct(wb, "cex_funded")
    if cex_count:
        lines.append(f"• CEX-funded: <b>{_count_pct(cex_count, cex_pct)}</b>")
        cex_raw = wb.get("cex_funded")
        breakdown = None
        if isinstance(cex_raw, dict):
            breakdown = cex_raw.get("by_exchange") or cex_raw.get("breakdown")
        if not breakdown:
            breakdown = wb.get("cex_breakdown") or wb.get("by_exchange")
        if breakdown:
            items = " | ".join(f"{esc(k)} {_num(v)}" for k, v in breakdown.items() if v)
            if items:
                lines.append(f"  {items}")

    ib_count, ib_pct = _num_pct(wb, "instant_buyers")
    if ib_count:
        emoji = _elevated(ib_pct)
        body = _count_pct(ib_count, ib_pct)
        lines.append(f"• Instant buyers (&lt;60s): <b>{body}</b>{(' ' + emoji) if emoji else ''}")

    fake = wb.get("fake_diversity_score") or wb.get("diversity_score")
    if fake:
        emoji = _elevated(fake, red=70)
        lines.append(
            f"• Fake diversity score: <b>{_num(fake)}/100</b>{(' ' + emoji) if emoji else ''}"
        )

    return "\n".join(lines)


def format_bundle_profile(bp: dict) -> str:
    """📦 Bundle Detection — clustered buying / deployer-funded activity."""
    if not isinstance(bp, dict) or not bp:
        return ""
    lines = ["📦 <b>Bundle Detection</b>"]

    df_count, df_pct = _num_pct(bp, "deployer_funded")
    if df_count:
        lines.append(f"• Deployer-funded wallets: <b>{_count_pct(df_count, df_pct)}</b>")

    snipers = bp.get("same_block_snipers") or bp.get("snipers")
    if snipers:
        lines.append(f"• Same-block snipers: <b>{_num(snipers)}</b>")

    jito = bp.get("jito_bundles") or bp.get("jito_bundles_detected")
    if jito:
        lines.append(f"• Jito bundles detected: <b>{_num(jito)}</b>")

    clusters = bp.get("behavior_clusters") or bp.get("clusters")
    if clusters:
        lines.append(f"• Behavior clusters: <b>{_num(clusters)}</b>")

    repeat = bp.get("cross_launch_repeat") or bp.get("cross_launch_repeat_wallets")
    if repeat:
        lines.append(f"• Cross-launch repeat wallets: <b>{_num(repeat)}</b>")

    risk_raw = bp.get("risk")
    if isinstance(risk_raw, dict):
        level = risk_raw.get("level") or bp.get("risk_level") or bp.get("risk_tier")
        score = risk_raw.get("score") or bp.get("risk_score") or bp.get("score")
    else:
        level = bp.get("risk_level") or bp.get("risk_tier")
        score = bp.get("risk_score") or bp.get("score")
    if level or score:
        emoji = _risk_emoji(level if level else score)
        if level:
            label = str(level).upper()
        elif score:
            label = _risk_tier_name(score).upper()
        else:
            label = "UNKNOWN"
        parts = [f"• Risk: <b>{esc(label)}</b>"]
        if score:
            parts.append(f"({_num(score)}/100)")
        if emoji:
            parts.append(emoji)
        lines.append(" ".join(parts))

    return "\n".join(lines)


def format_deployer_profile(dp: dict) -> str:
    """👤 Deployer Profile — deployer wallet history & behavior."""
    if not isinstance(dp, dict) or not dp:
        return ""
    lines = ["👤 <b>Deployer Profile</b>"]

    age = dp.get("wallet_age") or dp.get("age")
    if age:
        days = None
        if isinstance(age, dict):
            days = age.get("days")
            age_label = age.get("label") or (f"{_num(days)} days" if days else "")
        elif isinstance(age, (int, float)):
            days = age
            age_label = f"{_num(days)} days"
        else:
            age_label = str(age)
            match = re.match(r"(\d+)", age_label)
            if match:
                days = int(match.group(1))
        new_mark = " 🆕" if days is not None and days < 7 else ""
        lines.append(f"• Wallet age: <b>{esc(age_label)}</b>{new_mark}")

    deploys = dp.get("total_deployments") or dp.get("deployments") or dp.get("deploy_count")
    if deploys:
        lines.append(f"• Total deployments: <b>{_num(deploys)}</b>")

    rug = dp.get("rug_rate") or dp.get("rug_pct")
    if rug:
        emoji = _elevated(rug, red=50, yellow=30)
        lines.append(f"• Rug rate: <b>{_pct_int(rug)}</b>{(' ' + emoji) if emoji else ''}")

    funded = dp.get("funded_from") or dp.get("funding_source") or dp.get("funded_by")
    if funded:
        if isinstance(funded, (list, tuple)):
            funded_s = ", ".join(str(x) for x in funded if x)
        else:
            funded_s = str(funded)
        warn = " ⚠️" if _suspicious(funded_s) else ""
        lines.append(f"• Funded from: <b>{esc(funded_s)}</b>{warn}")

    chains = dp.get("chains") or dp.get("chains_deployed") or dp.get("chain")
    if chains:
        if isinstance(chains, (list, tuple)):
            chains_s = ", ".join(str(x) for x in chains if x)
        else:
            chains_s = str(chains)
        lines.append(f"• Chains: <b>{esc(chains_s)}</b>")

    sleep = dp.get("sleep_pattern") or dp.get("activity_pattern")
    if sleep:
        robot = " 🤖" if _botlike(str(sleep)) else ""
        lines.append(f"• Sleep pattern: <b>{esc(sleep)}</b>{robot}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Combined summary
# ─────────────────────────────────────────────────────────────────────────────


def _section(profile: dict, *keys: str) -> dict | None:
    """Return the dict under the first present key; None when absent.

    An explicitly-empty section ({} under the key) is returned as-is so the
    summary skips it — only a *missing* key triggers the flat-profile fallback.
    """
    for key in keys:
        if key in profile and isinstance(profile.get(key), dict):
            value: object = profile.get(key)
            return dict(value) if isinstance(value, dict) else None
    return None


# Flat fields that indicate a dict carries deep-scan profile data (as opposed
# to a generic scan response with just risk_score/summary/findings).
_PROFILE_FIELD_MARKERS = (
    "total_holders",
    "holders_total",
    "fresh_wallet",
    "cex_funded",
    "exchange_funded",
    "instant_buy",
    "fake_divers",
    "diversity_score",
    "deployer_funded",
    "same_block_sniper",
    "jito_bundle",
    "behavior_cluster",
    "cross_launch",
    "wallet_age",
    "total_deployments",
    "deploy_count",
    "rug_rate",
    "rug_pct",
    "funded_from",
    "funding_source",
    "funded_by",
    "chains",
    "sleep_pattern",
    "activity_pattern",
    "bundle_risk",
)


def _locate_sections(profile: dict) -> tuple[dict | None, dict | None, dict | None] | None:
    """Locate the three profile sections, or None when the dict is not a profile.

    Tries, in order: explicit section keys at the top level, the nested
    ``deep_profile`` bucket, then flat profile fields (whole dict is treated as
    the section). Returns None for generic scan responses (no typed profiles),
    which lets callers fall back to the legacy deep-scan report.
    """
    wb = _section(profile, "wallet_breakdown", "wallet_breakdown_data")
    bp = _section(profile, "bundle_profile", "bundle_detection")
    dp = _section(profile, "deployer_profile", "deployer")
    if wb is not None or bp is not None or dp is not None:
        return wb, bp, dp

    deep = profile.get("deep_profile")
    if isinstance(deep, dict):
        wb = _section(deep, "wallet_breakdown", "wallet_breakdown_data")
        bp = _section(deep, "bundle_profile", "bundle_detection")
        dp = _section(deep, "deployer_profile", "deployer")
        if wb is not None or bp is not None or dp is not None:
            return wb, bp, dp

    for key in profile:
        if any(key == marker or key.startswith(marker) for marker in _PROFILE_FIELD_MARKERS):
            return profile, profile, profile
    return None


def _risk_tier_name(score) -> str:
    from services.formatting import risk_tier

    return str(risk_tier(_num_float(score)))


def _verdict_line(profile: dict, bp: dict) -> str:
    """Build the 🛑/🟡/🟢 verdict line from risk fields or section signals."""
    level = str(profile.get("risk_level") or bp.get("risk_level") or "").lower()
    score = profile.get("risk_score") or profile.get("score") or bp.get("risk_score")

    if level in ("critical", "high", "danger", "severe") or (
        score is not None and float(score) >= 60
    ):
        emoji, label = "🛑", "HIGH RISK"
        default_tail = "Do not trade"
    elif level in ("medium", "caution", "warning") or (score is not None and float(score) >= 40):
        emoji, label = "🟡", "CAUTION"
        default_tail = "High manipulation risk — proceed carefully"
    else:
        emoji, label = "🟢", "LOW RISK"
        default_tail = "No major red flags — standard checks apply"

    verdict_text = profile.get("verdict") or profile.get("verdict_text")
    tail = esc(verdict_text) if verdict_text else default_tail
    return f"{emoji} <b>VERDICT: {label}</b> — {tail}"


def format_deep_scan_summary(profile: dict) -> list[str]:
    """🔬 DEEP SCAN — combine all sections into one (or more) Telegram messages.

    Returns a list of message bodies, each <= 4096 chars, so the caller can send
    them as separate messages. Returns [] when the input is not a deep-scan
    profile (no wallet/bundle/deployer sections anywhere), which lets callers
    fall back to the legacy deep-scan report.
    """
    located = _locate_sections(profile)
    if located is None:
        return []
    wb, bp, dp = located
    # Flat-profile fallback: if a section key is absent, treat the whole
    # profile as that section (each formatter only reads its own keys).
    if wb is None:
        wb = profile
    if bp is None:
        bp = profile
    if dp is None:
        dp = profile

    address = profile.get("address") or profile.get("token_address") or ""
    short_addr = _short(str(address)) if address else ""

    header = "🔬 <b>DEEP SCAN</b>"
    if short_addr:
        header += f" — <code>{esc(short_addr)}</code>"

    lines = [header, _HEADER_SEP]

    for section in (
        format_wallet_breakdown(wb),
        format_bundle_profile(bp),
        format_deployer_profile(dp),
    ):
        if "•" in section:
            lines.append("")
            lines.append(section)

    lines.append("")
    lines.append(_verdict_line(profile, bp))

    return _split_message("\n".join(lines))


def _split_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split a long report at paragraph boundaries, then line boundaries."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph.strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        flush()
        for line in paragraph.split("\n"):
            candidate = f"{current}\n{line}".strip() if current else line
            if len(candidate) <= limit:
                current = candidate
            else:
                flush()
                current = line
    flush()
    return chunks


# ─────────────────────────────────────────────────────────────────────────────
# Integration entrypoint — locate profiles in a deep-scan response and render.
# Used by handlers/scan.py to append the profile sections to a deep-scan report.
# ─────────────────────────────────────────────────────────────────────────────


def _find_profile(data: dict, key: str) -> dict | None:
    """Locate a profile dict from the response shape.

    Tries, in order: top-level ``data[key]``, ``data["deep_profile"][key]``,
    then the raw ``sentinel`` free/premium buckets.
    """
    if isinstance(data.get(key), dict):
        value: object = data.get(key)
        return dict(value) if isinstance(value, dict) else None
    deep = data.get("deep_profile")
    if isinstance(deep, dict) and isinstance(deep.get(key), dict):
        value = deep.get(key)
        return dict(value) if isinstance(value, dict) else None
    sentinel = data.get("sentinel")
    if isinstance(sentinel, dict):
        for bucket in ("premium", "free", "premium_unlocked"):
            payload = sentinel.get(bucket)
            if isinstance(payload, dict) and isinstance(payload.get(key), dict):
                value = payload.get(key)
                return dict(value) if isinstance(value, dict) else None
    return None


def _add(lines: list[str], label: str, value, fmt=None) -> None:
    """Append a key/value line, skipping absent values (keeps 0/False)."""
    if value is None or value == "" or value == [] or value == {}:
        return
    text = fmt(value) if fmt is not None else esc(_fmt_list(value))
    lines.append(f"  {label}: <b>{text}</b>")


def _fmt_list(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _render_wallet_section(wb: dict) -> str:
    """Rendering used by format_deepscan_profile_sections (deep_profile shape)."""
    lines = ["👛 <b>Wallet Breakdown</b>", thin_sep()]
    _add(lines, "Total holders", wb.get("total_holders"))
    _add(
        lines,
        "Fresh wallets (<7d)",
        wb.get("fresh_wallet_count"),
        fmt=lambda v: f"{v} ({_pct_frac(wb.get('fresh_wallet_pct', 0))})",
    )
    _add(
        lines,
        "Exchange-funded",
        wb.get("exchange_funded_count"),
        fmt=lambda v: f"{v} ({_pct_frac(wb.get('exchange_funded_pct', 0))})",
    )
    _add(
        lines,
        "Instant buys (<60s)",
        wb.get("instant_buy_count"),
        fmt=lambda v: f"{v} ({_pct_frac(wb.get('instant_buy_pct', 0))})",
    )
    exch = wb.get("exchange_breakdown")
    if isinstance(exch, dict) and exch:
        top = sorted(exch.items(), key=lambda kv: _num_float(kv[1]), reverse=True)[:5]
        lines.append("  🏦 CEX funding mix:")
        for name, count in top:
            lines.append(f"     • {esc(name)}: <b>{esc(count)}</b>")
    _add(
        lines,
        "Fake diversification",
        wb.get("fake_diversification_score"),
        fmt=lambda v: f"{_num_float(v):.0f}/100",
    )
    return "\n".join(lines)


def _render_bundle_section(bp: dict) -> str:
    """Rendering used by format_deepscan_profile_sections (deep_profile shape)."""
    lines = ["🧩 <b>Bundle Profile</b>", thin_sep()]
    _add(lines, "Bundles detected", bp.get("bundles_detected"))
    _add(
        lines,
        "Deployer-funded wallets",
        bp.get("deployer_funded_wallets"),
        fmt=lambda v: f"{v} ({_pct_frac(bp.get('deployer_funded_pct', 0))})",
    )
    _add(lines, "Same-block snipers", bp.get("same_block_snipers"))
    _add(lines, "Jito bundles", bp.get("jito_bundles"))
    _add(lines, "Behaviour clusters", bp.get("behavior_clusters"))
    _add(lines, "Repeat-launch wallets", bp.get("cross_launch_repeat_wallets"))
    if bp.get("bundle_risk_score") is not None or bp.get("bundle_risk_level"):
        score = _num_float(bp.get("bundle_risk_score"))
        level = str(bp.get("bundle_risk_level") or "unknown")
        lines.append(f"  ⚠️ Bundle risk: <b>{score:.0f}/100</b> ({esc(level.upper())})")
    return "\n".join(lines)


def _render_deployer_section(dp: dict) -> str:
    """Rendering used by format_deepscan_profile_sections (deep_profile shape)."""
    lines = ["🕸️ <b>Deployer Profile</b>", thin_sep()]
    _add(
        lines,
        "Wallet age",
        dp.get("wallet_age_days"),
        fmt=lambda v: f"{_num_float(v):.0f} days",
    )
    _add(lines, "Total deployments", dp.get("total_deployments"))
    _add(
        lines,
        "Rug rate",
        dp.get("rug_rate"),
        fmt=lambda v: f"{_pct_frac(v)} of deployments",
    )
    _add(lines, "Funded from", dp.get("funded_from"))
    _add(lines, "Chains active", dp.get("chains_active"))
    _add(lines, "Sleep pattern", dp.get("sleep_pattern"))
    return "\n".join(lines)


def format_deepscan_profile_sections(data: dict) -> list[str]:
    """Return rendered profile sections found in a deep-scan response.

    Only sections that are actually present in the response are returned, so
    callers can join them onto the base deep-scan report safely.
    """
    sections: list[str] = []
    for key, render in (
        ("wallet_breakdown", _render_wallet_section),
        ("bundle_profile", _render_bundle_section),
        ("deployer_profile", _render_deployer_section),
    ):
        profile = _find_profile(data, key)
        rendered = render(profile) if isinstance(profile, dict) and profile else ""
        if rendered:
            sections.append(rendered)
    return sections
