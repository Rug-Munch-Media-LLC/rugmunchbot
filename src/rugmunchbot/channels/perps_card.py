"""Render the daily perps recap card.

One card = data rows + one commentary paragraph + risk disclaimer + referral
disclosure + AT MOST ONE link. The single link is the GMGN perps CTA for the
market carrying the nearest liquidation band; when that market has no
verified/configured referral the card ships with the reason instead of a
link (``url: null``), never a guessed URL.

Copy doctrine (repo-wide): no generic "Alert" banner, no boilerplate footer.
The masthead names the desk, the rows name the number, the paragraph says what
the numbers mean for the next 24h.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.channels.formatters._primitives import NewsPost
from rugmunchbot.referrals import DISCLOSURE, Referral
from rugmunchbot.support.post_graphics import graphic_for

_HEADER = "\U0001f4ca <b>PERPS DESK</b>"
_SUB = "<i>Where the leverage sits, and what holding it costs tonight.</i>"
_RISK = (
    "\u26a0\ufe0f <b>Risk</b>: <i>perps are leveraged \u2014 a 10x position is "
    "liquidated by a ~10% move against it. Never size what you cannot lose.</i>"
)


def _usd(v: Any) -> str:
    """Compact USD: $1.24B / $14.2M / $940K. '?' when unparsable."""
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "?"
    if n >= 1e9:
        return f"${n / 1e9:.2f}B"
    if n >= 1e6:
        return f"${n / 1e6:.1f}M"
    if n >= 1e3:
        return f"${n / 1e3:.0f}K"
    return f"${n:,.0f}"


def _price(v: Any) -> str:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "?"
    return f"${n:,.2f}" if n < 1000 else f"${n:,.0f}"


def _funding(row: dict[str, Any]) -> str:
    """Funding clause — omitted entirely when the venue feed is missing."""
    rate = row.get("funding_rate")
    venue = row.get("funding_venue")
    if rate is None:
        return ""
    try:
        pct = float(rate) * 100
    except (TypeError, ValueError):
        return ""
    where = f" on {html.escape(str(venue))}" if venue else ""
    return f"funding {pct:+.4f}%/h{where}"


def _row_line(row: dict[str, Any]) -> str:
    sym = html.escape(str(row.get("symbol") or "?"))
    side = str(row.get("near_side") or "").upper()
    dist = row.get("distance_pct")
    stack = ""
    if side and dist is not None:
        stack = (
            f" \u00b7 10x <b>{html.escape(side)}</b> stack at "
            f"{_price(row.get('trigger_price'))} ({float(dist):.1f}% away)"
        )
    parts = [f"<b>{sym}</b> \u00b7 OI {_usd(row.get('oi_usd'))}{stack}"]
    funding = _funding(row)
    if funding:
        parts.append(funding)
    return "\n   ".join(parts)


def _cta_line(symbol: str, referral: Referral) -> str:
    """The one link (or the reason it is absent). Never a second link."""
    if referral.available and referral.url:
        url = html.escape(referral.url, quote=True)
        label = f"{html.escape(symbol.upper())} perps on GMGN"
        return f'\U0001f310 <a href="{url}">{label}</a> \u00b7 {DISCLOSURE}'
    reason = html.escape(str(referral.reason or "gmgn-perps:unavailable"))
    return (
        f"\U0001f310 <b>{html.escape(symbol.upper())}</b> perps link unavailable "
        f"({reason}) \u2014 verify the market on GMGN before trading."
    )


def render_perps_card(
    rows: list[dict[str, Any]],
    *,
    cta_symbol: str,
    referral: Referral,
    commentary: str,
) -> NewsPost:
    """Build the recap card for *rows* with exactly one CTA or a stated reason."""
    lines = [_HEADER, _SUB, ""]
    lines.extend(_row_line(r) for r in rows)
    lines.append("")
    if commentary:
        lines.append(html.escape(commentary, quote=False))
        lines.append("")
    lines.append(_RISK)
    lines.append(_cta_line(cta_symbol, referral))
    post = NewsPost("\n".join(lines))
    post.image_url = graphic_for("market")
    return post


__all__ = ["render_perps_card"]
