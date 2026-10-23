"""Render the buy-popup card. Pure: no I/O, no Telegram objects.

Every dynamic value passes through :func:`html.escape` — the stdlib canonical
escaper, so no new escaper module exists. The rendered text is truncated well
below Telegram's 4096-character ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rugmunchbot.buybot.models import BuybotConfig

__all__ = [
    "BuyEvent",
    "MarketSnapshot",
    "button_rows",
    "mint_ref",
    "render_card",
    "risk_badge",
]

MAX_CARD = 3900
ROW_LABELS = {
    "amount": "Amount",
    "usd": "Value",
    "price": "Price",
    "mcap": "MCap",
    "liquidity": "Liquidity",
    "risk": "Risk",
}


@dataclass(frozen=True)
class MarketSnapshot:
    """Whatever the market source could prove. ``None`` means "unknown", never 0."""

    price_usd: float | None = None
    mcap_usd: float | None = None
    liquidity_usd: float | None = None
    risk_score: int | None = None


@dataclass(frozen=True)
class BuyEvent:
    """One observed buy. ``usd_value`` is the source's number, not an estimate."""

    buyer: str
    usd_value: float | None = None
    amount_tokens: float = 0.0
    tx: str = ""


def risk_badge(score: int | None) -> str:
    """Badge for a 0-100 risk score; ``unrated`` when nothing scored it."""
    if score is None:
        return "⚪️ unrated"
    if score <= 30:
        return "🟢 low"
    if score <= 60:
        return "🟡 medium"
    if score <= 80:
        return "🟠 high"
    return "🔴 avoid"


def mint_ref(address: str) -> str:
    """Short, stable token reference small enough for a callback payload."""
    return (address or "")[:12]


def _usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:,.2f}K"
    return f"${value:,.2f}"


def _price(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1:
        return f"${value:,.4f}"
    return f"${value:.8f}".rstrip("0")


def _tokens(value: float) -> str:
    return f"{value:,.4f}".rstrip("0").rstrip(".")


def _rows(config: BuybotConfig, event: BuyEvent, market: MarketSnapshot) -> list[str]:
    values = {
        "amount": f"{_tokens(event.amount_tokens)} {escape(config.token.symbol)}"
        if config.token
        else "n/a",
        "usd": _usd(event.usd_value),
        "price": _price(market.price_usd),
        "mcap": _usd(market.mcap_usd),
        "liquidity": _usd(market.liquidity_usd),
        "risk": risk_badge(market.risk_score),
    }
    return [f"{ROW_LABELS[name]}: <b>{values[name]}</b>" for name in config.fields]


def render_card(
    config: BuybotConfig,
    event: BuyEvent,
    market: MarketSnapshot | None = None,
    *,
    note: str = "",
) -> str:
    """Render one popup card for *event* under *config* (HTML, escaped, bounded)."""
    token = config.token
    if token is None:
        return "No token configured — run /buybot token &lt;chain&gt; &lt;address&gt;."
    emoji, label = config.emojis.buy.emoji, config.emojis.buy.label
    lines = [
        f"{escape(emoji)} <b>{escape(label)}</b> — <b>{escape(token.symbol)}</b>"
        f" ({escape(config.chain.value)})",
        f"<code>{escape(token.address)}</code>",
        f"Buyer {escape(event.buyer or 'unknown')} bought "
        f"<b>{_tokens(event.amount_tokens)} {escape(token.symbol)}</b>",
        "",
        *_rows(config, event, market or MarketSnapshot()),
    ]
    if note:
        lines += ["", f"<i>{escape(note)}</i>"]
    return "\n".join(lines)[:MAX_CARD]


def button_rows(config: BuybotConfig, chat_id: int) -> tuple[tuple[str, str], ...]:
    """``(label, callback_data)`` rows: one button per preset, then action rows."""
    emoji, label = config.emojis.buy.emoji, config.emojis.buy.label
    ref = mint_ref(config.token.address) if config.token else "none"
    rows: list[tuple[str, str]] = []
    pair: list[tuple[str, str]] = []
    for amount in config.presets:
        pair.append((f"{emoji} {label} {_tokens(amount)}", f"buy|{chat_id}|{ref}|{amount:g}"))
        if len(pair) == 2:
            rows.append(tuple(pair))
            pair = []
    if pair:
        rows.append(tuple(pair))
    if config.buttons:
        rows.append(tuple((name.title(), f"tok|{chat_id}|{ref}|{name}") for name in config.buttons))
    return tuple(rows)
