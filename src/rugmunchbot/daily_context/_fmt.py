"""Daily market awareness formatters — pure rendering, unit-testable.

Extracted verbatim from the former single-module ``daily_context.py``.
"""

from __future__ import annotations

import contextlib
import re
from datetime import UTC, datetime
from html import escape
from typing import Any

from rugmunchbot.config import fmt_number
from rugmunchbot.daily_context._constants import CONTEXT_MAX_CHARS


def _fmt_price(value: Any) -> str:
    """Compact USD price: $67,123 / $3,512.45 / $0.0042. 'n/a' on junk."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if v >= 1000:
        return f"${v:,.0f}"
    if v >= 1:
        return f"${v:,.2f}"
    return f"${v:.4f}"


def _fmt_mcap(value: Any) -> str:
    """Market cap with a trillion suffix ($3.20T) above 1T; else fmt_number."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    return fmt_number(v)


def _trending_label(item: dict[str, Any]) -> str:
    """Render a trending token as 'Name (SYM) +12.3% [chain] — address'."""
    symbol = (item.get("symbol") or "").strip().upper()
    name = (item.get("name") or "").strip()
    addr = (item.get("token_address") or item.get("address") or "").strip()
    chain = (item.get("chain") or "").strip().lower()
    change = item.get("change_24h")

    # Build label: always show name + symbol
    if name and symbol:
        label = f"{name} ({symbol})"
    elif name:
        label = name
    elif symbol:
        label = symbol
    elif addr:
        label = f"{addr[:12]}..."
    else:
        label = "Unknown Token"

    # Add change
    if change is not None:
        with contextlib.suppress(TypeError, ValueError):
            label = f"{label} {float(change):+.1f}%"

    # Add chain
    if chain:
        label = f"{label} [{chain}]"

    # Add full address (only if it's a REAL scanable on-chain address —
    # 0x hex for EVM, base58 for Solana — never a coin ID like 'thinking-cat')
    if addr and re.fullmatch(r"(0x[a-fA-F0-9]{20,40}|[1-9A-HJ-NP-Za-km-z]{32,44})", addr):
        label = f"{label} — <code>{addr}</code>"

    return label


def _launch_label(item: dict[str, Any]) -> str:
    """Render a launch item as 'Name (SYM) [chain] — address'."""
    name = (item.get("name") or "").strip()
    symbol = (item.get("symbol") or "").strip()
    chain = (item.get("chain") or "").strip().lower()
    addr = (item.get("token_address") or item.get("address") or "").strip()

    # Build label: always show name + symbol
    if name and symbol:
        label = f"{name} ({symbol})"
    elif name:
        label = name
    elif symbol:
        label = symbol
    elif addr:
        label = f"{addr[:12]}..."
    else:
        label = "Unknown Token"

    # Add chain
    if chain:
        label = f"{label} [{chain}]"

    # Add full address
    if addr:
        label = f"{label} — <code>{addr}</code>"

    return label


def _has_content(data: dict[str, Any]) -> bool:
    """True when at least one market section produced usable data."""
    prices = data.get("prices") or {}
    return bool(
        prices.get("btc") is not None
        or prices.get("eth") is not None
        or data.get("total_mcap")
        or data.get("fear_greed_value") is not None
        or data.get("news")
        or data.get("trending")
        or data.get("launches")
    )


# ── Assembly-level formatters ───────────────────────────────────────────


def format_daily_context(data: dict[str, Any], when: datetime | None = None) -> str:
    """LLM-facing compact context block (the chat-injection contract).

    Format::

        CURRENT MARKET CONTEXT (Aug 9 2026): Bitcoin $67,123, ETH $3,512,
        Fear/Greed: 62 (Greed). Top news: <titles>. Trending: <tokens>.
        Recent launches: <launches>.

    Missing sections render as ``none`` / ``n/a`` so the LLM never sees a
    malformed block. Hard-capped at :data:`CONTEXT_MAX_CHARS`.
    """
    when = when or datetime.now(UTC)
    date_str = f"{when:%b} {when.day} {when.year}"
    prices = data.get("prices") or {}
    fg_value = data.get("fear_greed_value")
    fg_label = data.get("fear_greed_label") or "Neutral"
    news = data.get("news") or []
    trending = data.get("trending") or []
    launches = data.get("launches") or []

    price_bits: list[str] = []
    if prices.get("btc") is not None:
        price_bits.append(f"Bitcoin {_fmt_price(prices.get('btc'))}")
    if prices.get("eth") is not None:
        price_bits.append(f"ETH {_fmt_price(prices.get('eth'))}")
    if not price_bits:
        price_bits.append("Bitcoin n/a")
    fg_bit = f"Fear/Greed: {fg_value} ({fg_label})" if fg_value is not None else "Fear/Greed: n/a"

    context = (
        f"CURRENT MARKET CONTEXT ({date_str}): {', '.join(price_bits)}, {fg_bit}. "
        f"Top news: {'; '.join(news) if news else 'none'}. "
        f"Trending: {'; '.join(trending) if trending else 'none'}. "
        f"Recent launches: {'; '.join(launches) if launches else 'none'}."
    )
    return context[:CONTEXT_MAX_CHARS]


def format_market_section(data: dict[str, Any], when: datetime | None = None) -> str:
    """Compact user-facing "Today's Market" section for the /start card.

    HTML-escaped (the welcome card is sent with ``ParseMode.HTML``).
    """
    when = when or datetime.now(UTC)
    date_str = f"{when:%b} {when.day}"
    prices = data.get("prices") or {}
    fg_value = data.get("fear_greed_value")
    fg_label = data.get("fear_greed_label") or "Neutral"
    news = data.get("news") or []
    trending = data.get("trending") or []
    launches = data.get("launches") or []

    lines = [f"\U0001f4ca <b>Today's Market</b> ({date_str})"]
    bits: list[str] = []
    if prices.get("btc") is not None:
        bits.append(f"\U0001fa99 BTC {_fmt_price(prices.get('btc'))}")
    if prices.get("eth") is not None:
        bits.append(f"\U0001f680 ETH {_fmt_price(prices.get('eth'))}")
    if fg_value is not None:
        bits.append(f"\U0001f321\ufe0f F&G {fg_value} ({fg_label})")
    if data.get("total_mcap"):
        bits.append(f"\U0001f4c8 MCap {_fmt_mcap(data.get('total_mcap'))}")
    btc_dom = data.get("btc_dominance")
    if btc_dom is not None:
        with contextlib.suppress(TypeError, ValueError):
            bits.append(f"\U0001f4c9 BTC dom {float(btc_dom):.1f}%")
    if bits:
        lines.append(" | ".join(bits))
    if trending:
        lines.append("\U0001f525 " + " \u00b7 ".join(trending))
    if news:
        lines.append("\U0001f4f0 " + escape(news[0]))
    if launches:
        lines.append("\U0001f680 " + " \u00b7 ".join(launches))
    return "\n".join(lines)
