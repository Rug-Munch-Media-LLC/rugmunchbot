"""Full first-buyer list — the card's ``First buyers`` button view.

Renders the earliest buyers as ONE edited message (the card stays a single
message; the list replaces it in place and the card keyboard stays attached).
Rows carry only fields the payload had. A scan with no first-buyer payload
returns ``""`` so the caller shows an honest "no data" notice rather than an
empty table or a fabricated row.

One public entry point: :func:`full_buyers_view`.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._vcard_fields import num, short_addr
from rugmunchbot.formatters._vcard_freshbuyers import FRESH, addr_of, buyers_of, fresh_line

TELEGRAM_LIMIT = 4096
LIMIT = 10


def _row(rank: int, buyer: dict[str, Any]) -> str:
    """One buyer row: address, age, funder, bought, holding, realised."""
    parts = [f"<code>{html.escape(short_addr(addr_of(buyer)))}</code>"]
    age = num(buyer.get("wallet_age_days"))
    if age is not None:
        parts.append(f"{age:.1f}d")
    funder = str(buyer.get("funder_type") or "").strip()
    if funder:
        parts.append(html.escape(funder))
    bought = num(buyer.get("bought_sol"))
    if bought is not None:
        parts.append(f"bought {bought:.2f} SOL")
    held = num(buyer.get("current_pct"))
    if held is not None:
        parts.append(f"holds {held:.1f}%")
    sold = num(buyer.get("sold_pct"))
    if sold is not None and sold > 0:
        parts.append(f"sold {sold:.0f}%")
    realized = num(buyer.get("realized_sol"))
    if realized is not None and realized > 0:
        parts.append(f"realized {realized:.2f} SOL")
    return f"  {rank}. " + " \u00b7 ".join(parts)


def _ordered(r: dict[str, Any]) -> list[dict[str, Any]]:
    """Earliest first (that is what "first buyers" means); stable on ties."""
    buyers = buyers_of(r)
    return sorted(buyers, key=lambda b: str(b.get("first_buy_ts") or ""))


def full_buyers_view(
    r: dict[str, Any],
    *,
    limit: int = LIMIT,
    max_chars: int = TELEGRAM_LIMIT,
) -> str:
    """Top-``limit`` first buyers, fitted to ``max_chars`` (``""`` when empty)."""
    buyers = _ordered(r)
    if not buyers:
        return ""
    shown = buyers[:limit]
    symbol = html.escape(str(r.get("symbol") or "?"))
    head = [
        f"{FRESH} <b>First buyers</b> \u00b7 ${symbol} \u00b7 top {len(shown)}",
        f"  earliest {len(shown)} of {len(buyers)} tracked",
        "",
    ]
    rows = [_row(rank, buyer) for rank, buyer in enumerate(shown, 1)]
    summary = fresh_line(r)
    tail = ["", summary] if summary else []
    while rows:
        text = "\n".join([*head, *rows, *tail])
        if len(text) <= max_chars:
            return text
        rows.pop()
    return "\n".join([*head, *tail]).strip()
