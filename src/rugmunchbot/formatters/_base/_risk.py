"""Risk visualization — verdict headers, scoring breakdowns."""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._base._primitives import thin_sep

RISK_EMOJI = {"high": "\U0001f534", "medium": "\U0001f7e1", "low": "\U0001f7e2"}


RISK_VERDICT = {"high": "HIGH RISK", "medium": "CAUTION", "low": "LOW RISK"}


RISK_UNKNOWN_EMOJI = "\u26aa"


RISK_HIGH_SCORE = 70.0


RISK_MID_SCORE = 40.0


RISK_BAR_FILLED = "\u2b1b"


RISK_BAR_EMPTY = "\u2b1c"


def _risk_level(score: float) -> str:
    """Map a 0-100 risk score to a level key (high/medium/low)."""
    if score >= RISK_HIGH_SCORE:
        return "high"
    if score >= RISK_MID_SCORE:
        return "medium"
    return "low"


def _risk_indicator(score: float) -> str:
    """Color-coded risk emoji for a 0-100 score (🔴 / 🟡 / 🟢)."""
    return RISK_EMOJI[_risk_level(score)]


def _pct_emoji(value: float, *, high: float, mid: float) -> str:
    """Map a percentage to a risk emoji using per-metric thresholds.

    Thresholds are strict (``value > high`` is red) so boundary behavior
    matches the original funding-analysis rendering exactly.
    """
    if value > high:
        return RISK_EMOJI["high"]
    if value > mid:
        return RISK_EMOJI["medium"]
    return RISK_EMOJI["low"]


def _verdict_sentence(r: dict[str, Any]) -> str:
    """Pick the single most important finding for the verdict headline.

    Only reads keys that actually exist in ``r`` — absent keys are skipped
    (never treated as ``False``), so a missing field cannot produce a false
    claim. Priority: honeypot > mintable > freeze/blacklist > bundled launch
    > ownership > unverified > thin LP > wash trading; otherwise a positive
    confirmation of what DID pass (real signal — never a generic filler).
    """
    if r.get("honeypot") is True:
        return "Honeypot detected \u2014 selling may be blocked; extreme caution advised."
    if r.get("mintable") is True:
        return "Supply is mintable \u2014 the owner can print unlimited new tokens."
    if r.get("can_blacklist") is True or r.get("freezable") is True:
        return "Owner can blacklist or freeze wallets \u2014 holders can be locked out of selling."
    ba = r.get("bundle_analysis")
    if isinstance(ba, dict) and ba.get("is_bundled"):
        n = ba.get("launch_buyers")
        count = f" ({n} wallets)" if isinstance(n, (int, float)) and n else ""
        return f"Launch was bundled{count} \u2014 early volume is coordinated, not organic."
    if r.get("owner_renounced") is False:
        return "Ownership is not renounced \u2014 the deployer retains contract control."
    if r.get("contract_verified") is False:
        return "Contract source is not verified \u2014 code cannot be audited."
    liquidity = r.get("liquidity_usd") or r.get("liquidity")
    try:
        liq_float = float(liquidity) if liquidity is not None else None
    except (TypeError, ValueError):
        liq_float = None
    if liq_float is not None and liq_float < 10_000:
        return "Liquidity is thin \u2014 large price swings and exit issues possible."
    rs_raw = r.get("risk_signals")
    rs: dict[str, Any] = rs_raw if isinstance(rs_raw, dict) else {}
    if rs.get("wash_trading") is True:
        return "Wash-trading signals present \u2014 treat reported volume with suspicion."
    # Nothing fired — confirm what passed (positive confirmations are signal).
    passed: list[str] = []
    if r.get("honeypot") is False:
        passed.append("sellable")
    if r.get("owner_renounced") is True or r.get("mintable") is False:
        passed.append("mint renounced")
    if r.get("freezable") is False or r.get("can_blacklist") is False:
        passed.append("freeze renounced")
    if isinstance(ba, dict) and ba.get("is_bundled") is False:
        passed.append("no launch bundle")
    if len(passed) >= 2:
        return "Core checks passed \u2014 " + ", ".join(passed[:3]) + "."
    return "No critical flags \u2014 normal caution advised."


def format_verdict_header(r: dict[str, Any]) -> str:
    """Big color-coded verdict headline for a scan report.

    Renders a large emoji + one-word verdict + score line (e.g.
    ``🔴 <b>HIGH RISK</b> — Score 82/100``) followed by a one-line
    plain-English sentence naming the single most important finding.
    A missing/unparseable ``risk_score`` renders a neutral UNKNOWN line
    instead of a misleading 0/100 — this function never fabricates data.
    """
    raw = r.get("risk_score")
    try:
        score = float(raw) if raw is not None else None
    except (TypeError, ValueError):
        score = None
    if score is None:
        headline = f"{RISK_UNKNOWN_EMOJI} <b>UNKNOWN</b> \u2014 no risk score available"
    else:
        level = _risk_level(score)
        headline = f"{RISK_EMOJI[level]} <b>{RISK_VERDICT[level]}</b> \u2014 Score {score:.0f}/100"
    return f"{headline}\n{_verdict_sentence(r)}"


def _price_diff_pct(p1: float, p2: float) -> str:
    if p1 <= 0 or p2 <= 0:
        return "N/A"
    diff = ((p1 - p2) / max(p1, p2)) * 100
    abs_diff = abs(diff)
    if abs_diff < 0.01:
        return "\u2248 same"
    cheaper = "T1" if diff < 0 else "T2"
    return f"{cheaper} {abs_diff:.2f}% cheaper"


DIMENSION_LABELS = {
    "security": "\U0001f512 Contract Security",
    "liquidity": "\U0001f4a7 Liquidity",
    "holders": "\U0001f465 Holders",
    "deployer": "\U0001f468\u200d\U0001f4bb Deployer",
    "market": "\U0001f4ca Market",
}


def format_scoring_breakdown(breakdown: dict[str, Any]) -> str:
    """Render the v2 explainable per-dimension scoring breakdown.

    Each dimension carries score/max/delta/flags. Output is a compact
    table with a color-coded risk bar (🔴 high / 🟡 medium / 🟢 low per
    dimension) so users can see WHY a token scored the way it did at a
    glance. Delta arrows (▲/▼) show the change direction when present.
    """
    if not breakdown:
        return ""
    lines = ["", "\U0001f9e9 <b>Scoring Breakdown</b>", thin_sep()]
    bar_len = 10
    for dim, label in DIMENSION_LABELS.items():
        entry = breakdown.get(dim)
        if not isinstance(entry, dict):
            continue
        score = float(entry.get("score", 0) or 0)
        maximum = float(entry.get("max", 0) or 0)
        delta = float(entry.get("delta", 0) or 0)
        filled = round((score / maximum) * bar_len) if maximum else 0
        filled = max(0, min(filled, bar_len))
        bar = RISK_BAR_FILLED * filled + RISK_BAR_EMPTY * (bar_len - filled)
        ratio = (score / maximum) if maximum else 0.0
        marker = RISK_EMOJI[_risk_level(ratio * 100)]
        delta_str = ""
        if delta > 0:
            delta_str = " \u25b2"
        elif delta < 0:
            delta_str = " \u25bc"
        lines.append(
            f"  {marker} {label:<22} <code>{bar}</code> <b>{score:.0f}/{maximum:.0f}</b>{delta_str}"
        )
        flags = entry.get("flags") or []
        if flags:
            for fl in flags[:2]:
                lines.append(f"      \u26a0\ufe0f {fl}")
    return "\n".join(lines)


def format_funding_analysis(r: dict[str, Any]) -> str:
    """Render the funding analysis block (exchange-funded / fresh / scam overlap).

    Reads the convenience keys populated by the scanner layer:
    ``exchange_funded_pct``, ``fresh_wallet_pct``, ``holder_overlap_pct``.
    Missing values render as ⚪ N/A so the section is stable across v1/v2.
    Colors map through ``RISK_EMOJI`` with per-metric thresholds
    (exchange 50/30, fresh 40/20, overlap 30/10).
    """
    exchange = r.get("exchange_funded_pct")
    fresh = r.get("fresh_wallet_pct")
    overlap = r.get("holder_overlap_pct")
    if exchange is None and fresh is None and overlap is None:
        return ""
    lines = ["", "\U0001f4b8 <b>Funding Analysis</b>", thin_sep()]
    if exchange is not None:
        emoji = _pct_emoji(exchange, high=50, mid=30)
        lines.append(f"  {emoji} Exchange-Funded:        <b>{exchange:.0f}%</b>")
    else:
        lines.append(f"  {RISK_UNKNOWN_EMOJI} Exchange-Funded:        <b>N/A</b>")
    if fresh is not None:
        emoji = _pct_emoji(fresh, high=40, mid=20)
        lines.append(f"  {emoji} Fresh Wallets:           <b>{fresh:.0f}%</b>")
    else:
        lines.append(f"  {RISK_UNKNOWN_EMOJI} Fresh Wallets:           <b>N/A</b>")
    if overlap is not None:
        emoji = _pct_emoji(overlap, high=30, mid=10)
        lines.append(f"  {emoji} Holder Overlap w/ Scams: <b>{overlap:.0f}%</b>")
    else:
        lines.append(f"  {RISK_UNKNOWN_EMOJI} Holder Overlap w/ Scams: <b>N/A</b>")
    return "\n".join(lines)
