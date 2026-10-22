"""CEX-funded fresh wallets + holder quality for the scan card.

"Funded by CEX" is NOT "exchange holdings". This block counts wallets BORN
inside the window whose first inbound transfer came from an exchange hot
wallet — an on-ramp buyer (sell pressure waiting to happen), never custody of
supply. The card keeps the two words apart on purpose.

Additive data contract (every field optional)::

    cex_funded_fresh{count, pct_of_holders, pct_of_supply, pct_of_first_buyers,
                     median_age_hours, window_h, top_funders[]}
    funding_mix{cex, deployer, bridge, mixer, wallet, unknown}
    recent_funding{wallets_n, pct_of_holders, window_h, funded_by_cex,
                   funded_by_deployer}
    holder_quality{exchange_pct, fresh_pct, insider_pct, bundle_pct, kol_pct,
                   dev_pct, whale_pct}

Rules: absent block -> nothing rendered (never a fabricated 0%); a share of
supply with no denominator (partial holder set) renders
``— (partial holder set)`` instead of a percentage that would read as
complete; percent values <= 1 are treated as fractions, larger values as
percent units.

One public entry point: :func:`funding_lines`.
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.formatters._vcard_fields import DASH, num

CEX = "\U0001f3e6\u27a1\ufe0f\U0001f464"  # 🏦➡️👤
HOLDERS = "\U0001f465"
MONEY = "\U0001f4b8"
PARTIAL = f"{DASH} (partial holder set)"
#: Holder-quality keys in reading order (exchange-held supply is NOT listed —
#: it is custody, not a fresh funded wallet).
QUALITY = (
    ("fresh_pct", "fresh"),
    ("insider_pct", "insiders"),
    ("bundle_pct", "bundle"),
    ("kol_pct", "KOL"),
    ("dev_pct", "dev"),
    ("whale_pct", "whale"),
)
MIX = ("cex", "deployer", "bridge", "mixer", "wallet", "unknown")
#: Hard cap for this optional group; the card drops it before anything else.
MAX_LINES = 4


def _pct(value: Any) -> str | None:
    """Percent string (fraction <=1 scaled), ``None`` when not measured."""
    number = num(value)
    if number is None:
        return None
    return f"{number * 100:.0f}%" if number <= 1 else f"{number:.0f}%"


def _funders(block: Any, limit: int = 3) -> list[str]:
    """``Binance (4 wallets)`` rows from ``top_funders``."""
    rows: list[str] = []
    funders = block.get("top_funders") if isinstance(block, dict) else None
    if not isinstance(funders, list):
        return rows
    for funder in funders[:limit]:
        if not isinstance(funder, dict):
            continue
        name = str(funder.get("name") or funder.get("label") or funder.get("address") or "")
        if not name.strip():
            continue
        count = num(
            funder.get("count") if funder.get("count") is not None else funder.get("wallets")
        )
        row = html.escape(name.strip())
        rows.append(f"{row} ({int(count)} wallets)" if count is not None else row)
    return rows


def cex_line(r: dict[str, Any]) -> str:
    """``🏦➡️👤 18% of holders are <7d wallets funded by CEX (9 wallets · …)``."""
    block = r.get("cex_funded_fresh")
    if not isinstance(block, dict) or not block:
        return ""
    pct = _pct(block.get("pct_of_holders"))
    count = num(block.get("count"))
    if pct:
        head = f"{pct} of holders are <7d wallets funded by CEX"
    elif count is not None:
        head = f"{int(count)} <7d wallets funded by CEX"
    else:
        return ""
    detail: list[str] = []
    if count is not None and pct:
        detail.append(f"{int(count)} wallets")
    supply = _pct(block.get("pct_of_supply"))
    detail.append(f"{supply} supply" if supply else f"supply {PARTIAL}")
    first = _pct(block.get("pct_of_first_buyers"))
    if first:
        detail.append(f"{first} of first buyers")
    median = num(block.get("median_age_hours"))
    if median is not None:
        detail.append(f"median {median:.0f}h")
    return f"{CEX} {head} (" + " \u00b7 ".join(detail) + ")"


def funders_line(r: dict[str, Any]) -> str:
    """``Top funder: Binance (4 wallets)`` — only when funders were resolved."""
    rows = _funders(r.get("cex_funded_fresh"))
    if not rows:
        return ""
    label = "Top funder" if len(rows) == 1 else "Top funders"
    return f"  {MONEY} {label}: " + " \u00b7 ".join(rows)


def holders_line(r: dict[str, Any]) -> str:
    """``👥 Holders: fresh 38% · insiders 9% · bundle 6% · KOL 4% · dev 2%``."""
    quality = r.get("holder_quality")
    if not isinstance(quality, dict) or not quality:
        return ""
    bits = [
        f"{label} {pct}" for key, label in QUALITY if (pct := _pct(quality.get(key))) is not None
    ]
    return f"{HOLDERS} Holders: " + " \u00b7 ".join(bits) if bits else ""


def recent_line(r: dict[str, Any]) -> str:
    """``💸 14 wallets funded in 24h (7 via CEX)``."""
    block = r.get("recent_funding")
    if not isinstance(block, dict) or not block:
        return ""
    wallets = num(block.get("wallets_n"))
    if wallets is None:
        return ""
    window = num(block.get("window_h"))
    when = f"in {int(window)}h" if window else "recently"
    bits: list[str] = []
    for key, label in (("funded_by_cex", "via CEX"), ("funded_by_deployer", "by deployer")):
        value = num(block.get(key))
        if value is not None:
            bits.append(f"{int(value)} {label}")
    joined = " \u00b7 ".join(bits)
    tail = f" ({joined})" if joined else ""
    return f"{MONEY} {int(wallets)} wallets funded {when}{tail}"


def mix_line(r: dict[str, Any]) -> str:
    """``  Mix: cex 40% · deployer 20% · bridge 10%`` — pinned order."""
    mix = r.get("funding_mix")
    if not isinstance(mix, dict) or not mix:
        return ""
    bits = [f"{key} {pct}" for key in MIX if (pct := _pct(mix.get(key))) is not None]
    return "  Mix: " + " \u00b7 ".join(bits) if bits else ""


def funding_lines(r: dict[str, Any]) -> list[str]:
    """The card's CEX / holder-quality group (``[]`` when nothing is present)."""
    lines = [
        line
        for line in (cex_line(r), funders_line(r), holders_line(r), recent_line(r), mix_line(r))
        if line
    ][:MAX_LINES]
    return ["", *lines] if lines else []


def funding_detail_lines(r: dict[str, Any]) -> list[str]:
    """Full-report variant: every top funder plus the mix on its own line."""
    lines = [line for line in (cex_line(r),) if line]
    funders = _funders(r.get("cex_funded_fresh"), limit=10)
    if funders:
        label = "Top funder" if len(funders) == 1 else "Top funders"
        lines.append(f"  {MONEY} {label}:")
        lines.extend(f"    \u2022 {row}" for row in funders)
    lines.extend(line for line in (holders_line(r), recent_line(r), mix_line(r)) if line)
    return lines
