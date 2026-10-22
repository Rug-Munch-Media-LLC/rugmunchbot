"""Cohort / KOL / impact rollup for the scan card (parity sections).

The three sections are produced by the standalone scanner service (see
``tools/scanner/_parity.py``): launch-cohort economics, KOL flow and exit
depth — the blocks the bot's own scan never had. Rendering rules:

* a section that answered ``{"reason": ...}`` renders ONE em-dash line plus
  the reason — never an invented zero;
* the cohort rollup lists Team / Insiders / Snipers as count, init→now % and
  realised SOL (percent values arrive already in percent units);
* the KOL line renders only when at least one registry wallet matched;
* the impact line renders only when a DexScreener pair backed it.

A payload with none of the three keys (v1 scan, parity leg never ran) renders
nothing at all, so existing cards are byte-identical.

One public entry point: :func:`format_parity_section`.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.formatters._base import thin_sep
from rugmunchbot.formatters._vcard_funding import funding_detail_lines

DASH = "\u2014"
HEADING = "\U0001f4ca <b>Launch Cohorts</b>"
FUND_HEADING = "\U0001f3e6\u27a1\ufe0f\U0001f464 <b>CEX-Funded Fresh Wallets</b>"
COHORTS = ("team", "insiders", "snipers")
LABELS = {"team": "Team", "insiders": "Insiders", "snipers": "Snipers"}


def _pct(value: Any) -> str:
    """Percent string for a value already expressed in percent units."""
    if value is None:
        return DASH
    try:
        return f"{float(value):.4f}%"
    except (TypeError, ValueError):
        return DASH


def _sol(value: Any) -> str:
    """SOL amount with 6 dp (the service rounds realised PnL to 6)."""
    if value is None:
        return DASH
    try:
        return f"{float(value):.6f} SOL"
    except (TypeError, ValueError):
        return DASH


def _reason(section: Any) -> str | None:
    """The section's own reason string, when it carries one."""
    if isinstance(section, dict):
        reason = section.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return None


def _clip(text: str, limit: int = 90) -> str:
    """One-line, length-bounded reason text."""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "\u2026"


def _cohort_line(name: str, block: Any) -> str:
    """One cohort row: count, initial→current position, realised PnL."""
    if not isinstance(block, dict):
        return f"  \u2022 {LABELS[name]}: {DASH}"
    count = block.get("count")
    bundled = block.get("bundled_n")
    count_str = DASH if count is None else str(count)
    if isinstance(bundled, int) and bundled > 0:
        count_str += f" ({bundled} bundled)"
    return (
        f"  \u2022 {LABELS[name]}: <b>{count_str}</b>"
        f" | {_pct(block.get('initial_buy_pct'))} \u2192 {_pct(block.get('current_pct'))}"
        f" | realised {_sol(block.get('realized_sol'))}"
    )


def _kol_line(kol: Any) -> str | None:
    """KOL row — only when a registry wallet actually traded the token."""
    matched = kol.get("kol") if isinstance(kol, dict) else None
    if not matched:
        return None
    handles = [
        str(w.get("handle") or w.get("wallet", "")[:6]) for w in matched if isinstance(w, dict)
    ]
    shown = ", ".join(h for h in handles[:3] if h)
    more = f" +{len(handles) - 3}" if len(handles) > 3 else ""
    sold = sum(float(w.get("sold_sol") or 0.0) for w in matched if isinstance(w, dict))
    bought = sum(float(w.get("bought_sol") or 0.0) for w in matched if isinstance(w, dict))
    return (
        f"  \u2022 KOL: <b>{len(matched)}</b> matched \u2014 {shown}{more}"
        f" | bought {_sol(bought)} / sold {_sol(sold)}"
    )


def _impact_line(impact: Any) -> str | None:
    """Exit-depth row — only when a DexScreener pair backed it."""
    if not isinstance(impact, dict) or not impact.get("pair_address"):
        return None
    buy, sell = impact.get("buy_1sol_pct"), impact.get("sell_1sol_pct")
    quote = impact.get("quote_symbol") or "?"
    depth = impact.get("depth_2pct_usd")
    depth_str = DASH if depth is None else f"${float(depth):,.0f}"
    if buy is None or sell is None:
        one_sol = f"{DASH} (quote {quote})"
    else:
        one_sol = f"{float(buy):.6f}% / {float(sell):.6f}%"
    return f"  \u2022 Impact: 1 SOL buy/sell <b>{one_sol}</b> | 2% depth {depth_str}"


def format_parity_section(r: dict[str, Any]) -> list[str]:
    """Render the parity block, or ``[]`` when the payload has none of it.

    Args:
        r: Scan report payload.

    Returns:
        Lines to extend the report with (heading, rule, rows). Empty when no
        parity key is present, so v1 / vendored-only cards are unchanged.
    """
    if not any(key in r for key in ("cohorts", "kol", "impact")):
        return []
    body: list[str] = []
    cohorts = r.get("cohorts")
    if isinstance(cohorts, dict) and cohorts:
        reason = _reason(cohorts)
        if reason:
            body.append(f"  \u2022 Cohorts: {DASH} ({_clip(reason)})")
        else:
            body.extend(_cohort_line(name, cohorts.get(name)) for name in COHORTS)
    kol_line = _kol_line(r.get("kol"))
    if kol_line:
        body.append(kol_line)
    impact_line = _impact_line(r.get("impact"))
    if impact_line:
        body.append(impact_line)
    funding = funding_detail_lines(r)
    head_block = ["", FUND_HEADING, *funding] if funding else []
    if not body:
        return head_block
    return [*head_block, "", HEADING, thin_sep(), *body]


TELEGRAM_LIMIT = 4096


def fit_report(text: str, limit: int = TELEGRAM_LIMIT) -> str:
    """Keep a rendered report inside Telegram's limit without breaking HTML.

    The parity block is the only optional section added late, so drop it first.
    If the report is still over, return the original: truncating an HTML string
    mid-tag would make Telegram reject the whole message.
    """
    if len(text) <= limit:
        return text
    out: list[str] = []
    skipping = False
    for line in text.split("\n"):
        if line.strip() == HEADING:
            skipping = True
            continue
        if skipping and not line.strip():
            skipping = False
            continue
        if skipping:
            continue
        out.append(line)
    trimmed = "\n".join(out)
    return trimmed if len(trimmed) <= limit else text
