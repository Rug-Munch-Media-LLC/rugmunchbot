"""Multi-chain /price card renderer — pure formatting, zero I/O.

One renderer for every chain the pricing service covers (Solana, Ethereum,
BSC, Polygon, Arbitrum, Base, ...). Every number comes from the caller's
``PriceResult`` / cached payload; a field the winning source did not report
renders as ``—`` WITH the reason — never as a fabricated zero.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rugmunchbot.config import WEBSITE_URL, fmt_chain, fmt_number, fmt_pct
from rugmunchbot.formatters import detect_chain, dexscreener_url, footer_links, thin_sep

MISSING = "\u2014"


def _get(data: Any, key: str) -> Any:
    """Read one field from a PriceResult OR its flat ``to_dict()`` payload."""
    if isinstance(data, Mapping):
        return data.get(key)
    return getattr(data, key, None)


def _missing(source: str, label: str) -> str:
    return f"{MISSING} <i>{label} not reported by {source}</i>"


def _money(data: Any, key: str, label: str, source: str) -> str:
    value = _get(data, key)
    if value is None or value == 0:
        return _missing(source, label)
    try:
        return fmt_number(float(value))
    except (TypeError, ValueError):
        return _missing(source, label)


def _change(data: Any, source: str) -> str:
    value = _get(data, "change_24h")
    if value is None:
        return _missing(source, "24h change")
    try:
        return fmt_pct(float(value))
    except (TypeError, ValueError):
        return _missing(source, "24h change")


def _timestamp(fetched_at: str | None) -> str:
    """Render the fetch timestamp (provenance) as ``YYYY-MM-DD HH:MM UTC``."""
    if not fetched_at:
        return "unknown <i>(not stamped by the caller)</i>"
    return fetched_at.replace("T", " ")[:16] + " UTC"


def render_price_card(
    *,
    address: str,
    chain: str = "",
    data: Any,
    fetched_at: str | None = None,
) -> str:
    """Render the multi-chain price card for /price.

    Args:
        address: Contract address shown verbatim in ``<code>``.
        chain: Chain slug; auto-detected from the address when empty.
        data: ``PriceResult`` or the flat dict cached in Redis.
        fetched_at: ISO timestamp of the fetch (cache hits keep the original).
    """
    source = str(_get(data, "source") or "unavailable")
    chain_name = chain or detect_chain(address) or "unknown"
    lines = [
        "\U0001f4b0 <b>Token Price</b>",
        thin_sep(),
        f"<code>{address}</code>",
        fmt_chain(chain_name),
        "",
        f"\U0001f4b5 Price: <b>{_money(data, 'price_usd', 'price', source)}</b>",
        f"\U0001f4ca Market cap: {_money(data, 'market_cap', 'market cap', source)}",
        f"\U0001f4a7 Liquidity: {_money(data, 'liquidity', 'liquidity', source)}",
        f"\U0001f4c8 24h change: {_change(data, source)}",
        f"\U0001f501 Volume 24h: {_money(data, 'volume_24h', 'volume', source)}",
        "",
        f"\U0001f4e1 Source: {source}",
        f"\U0001f552 Fetched: {_timestamp(fetched_at)}",
        f'\U0001f4c9 Chart: <a href="{dexscreener_url(address, chain_name)}">DexScreener</a>'
        f' \u00b7 <a href="{WEBSITE_URL}/markets/token/{address}">RugMunch</a>',
        footer_links(address, chain_name),
    ]
    return "\n".join(lines)
