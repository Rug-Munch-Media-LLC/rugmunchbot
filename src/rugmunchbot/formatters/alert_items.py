"""Render one queued webhook alert item as Telegram HTML.

``alert_worker`` owns delivery; this module owns what a delivered alert looks
like. Every renderer is total — an unknown ``type`` or a garbage payload draws a
fallback card instead of raising, because a formatting error must never cost a
delivery retry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rugmunchbot.config import fmt_number
from rugmunchbot.formatters import footer_links, sep, short_addr


@dataclass(frozen=True)
class _Card:
    """The fields every renderer reads, resolved once from the payload."""

    etype: str
    payload: dict[str, Any]
    addr: str
    symbol: str
    chain: str
    header: str


def _card(item: dict[str, Any]) -> _Card:
    payload = item.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    addr = str(payload.get("token_address") or payload.get("address") or "").lower()
    symbol = str(payload.get("token_symbol") or payload.get("symbol") or "").upper()
    if not symbol:
        symbol = short_addr(addr) if addr else "???"
    return _Card(
        etype=str(item.get("type") or "unknown"),
        payload=payload,
        addr=addr,
        symbol=symbol,
        chain=str(payload.get("chain") or ""),
        header=f"{sep()}\n\n",
    )


def _money(value: Any) -> str:
    """Format a USD value — ``fmt_number`` already includes the ``$`` sign."""
    try:
        return fmt_number(float(value or 0))
    except (ValueError, TypeError):
        return "$0"


_CHAINS = {"ethereum": "ETH", "solana": "SOL", "bsc": "BSC", "polygon": "MATIC", "base": "BASE"}
_ARROWS = {"up": "\U0001f4c8", "down": "\U0001f4c9"}


def _chain(chain: str) -> str:
    return _CHAINS.get((chain or "").lower(), (chain or "unknown").upper())


def _scan_line(addr: str) -> str:
    return f"\U0001f50d <b>Scan:</b> /scan <code>{addr.lower()}</code>" if addr else ""


def _whale(c: _Card) -> str:
    p = c.payload
    return (
        f"\U0001f40b <b>Whale Move!</b>\n{c.header}"
        f"<b>{c.symbol}</b> <code>{c.addr}</code>\n\n"
        f"\U0001f4b0 <b>Value:</b> {_money(p.get('value_decimal'))}\n"
        f"\U0001f464 <b>From:</b> <code>{p.get('from_address', '')}</code>\n"
        f"\U0001f3e6 <b>To:</b> <code>{p.get('to_address', '')}</code>\n"
        f"\U0001f4c1 <b>Chain:</b> {_chain(c.chain)}\n"
    )


def _price(c: _Card) -> str:
    p = c.payload
    direction = str(p.get("direction") or "")
    arrow = _ARROWS.get(direction, "\U0001f4ca")
    return (
        f"{arrow} <b>Price Alert: {c.symbol}</b>\n{c.header}"
        f"<b>{c.symbol}</b> <code>{c.addr}</code>\n\n"
        f"\U0001f4ca <b>Change:</b> {p.get('change_pct') or '0'}% ({direction or 'move'})\n"
        f"\U0001f4b5 <b>Price:</b> {_money(p.get('price'))}\n"
        f"\U0001f4c1 <b>Chain:</b> {_chain(c.chain)}\n"
    )


def _liquidity(c: _Card) -> str:
    p = c.payload
    method = str(p.get("detection_method") or "")
    return (
        f"\U0001f4a7 <b>Liquidity Change: {c.symbol}</b>\n{c.header}"
        f"<b>{c.symbol}</b> <code>{c.addr}</code>\n\n"
        f"\U0001f4b0 <b>Value:</b> {_money(p.get('value_decimal'))}\n"
        + (f"\U0001f4cb <b>Source:</b> {method}\n" if method else "")
        + f"\U0001f4c1 <b>Chain:</b> {_chain(c.chain)}\n"
    )


def _deployed(c: _Card) -> str:
    p = c.payload
    name = str(p.get("token_name") or "")
    deployer = p.get("deployer_address", "")
    return (
        f"\U0001f195 <b>New Token Launched</b>\n{c.header}"
        f"<b>{c.symbol}</b> <code>{c.addr}</code>\n"
        + (f"\U0001f3f7\ufe0f <b>Name:</b> {name}\n" if name else "")
        + (f"\U0001f464 <b>Deployer:</b> <code>{deployer}</code>\n" if deployer else "")
        + f"\U0001f4c1 <b>Chain:</b> {_chain(c.chain)}\n"
    )


def _first_swap(c: _Card) -> str:
    return (
        f"\U0001f504 <b>First Swap: {c.symbol}</b>\n{c.header}"
        f"<b>{c.symbol}</b> <code>{c.addr}</code>\n\n"
        f"\U0001f4b0 <b>Value:</b> {_money(c.payload.get('value_decimal'))}\n"
        f"\U0001f4c1 <b>Chain:</b> {_chain(c.chain)}\n"
    )


def _wallet(c: _Card) -> str:
    p = c.payload
    return (
        f"\U0001f45b <b>Wallet Activity</b>\n{c.header}"
        f"\U0001f464 <b>From:</b> <code>{p.get('from_address', '')}</code>\n"
        f"\U0001f3e6 <b>To:</b> <code>{p.get('to_address', '')}</code>\n"
        f"\U0001f4b0 <b>Value:</b> {_money(p.get('value_decimal'))}\n"
        f"\U0001f4c1 <b>Chain:</b> {_chain(c.chain)}\n"
    )


def _monitor(c: _Card) -> str:
    changes = c.payload.get("changes") or []
    changes_text = (
        "\n".join(f"\u2022 {change}" for change in changes)
        if changes
        else "Security properties changed."
    )
    return (
        f"\U0001f6f0\ufe0f <b>Monitor Change: {c.symbol}</b>\n{c.header}"
        f"<b>{c.symbol}</b> <code>{c.addr}</code>\n\n"
        f"{changes_text}\n"
        f"\U0001f4c1 <b>Chain:</b> {_chain(c.chain)}\n"
    )


def _generic(c: _Card) -> str:
    return f"\U0001f514 <b>Alert: {c.symbol}</b>\n{c.header}\U0001f4c1 <b>Chain:</b> {_chain(c.chain)}\n"


_RENDERERS = {
    "token.whale_move": _whale,
    "token.price_change": _price,
    "token.liquidity_change": _liquidity,
    "token.deployed": _deployed,
    "token.first_swap": _first_swap,
    "wallet.activity": _wallet,
    "token.monitor_change": _monitor,
}


def format_alert(item: dict[str, Any]) -> str:
    """Build the HTML alert text for a queued event (provider payload)."""
    card = _card(item)
    text = _RENDERERS.get(card.etype, _generic)(card)
    scan = _scan_line(card.addr)
    return text + (f"{scan}\n" if scan else "") + footer_links()
