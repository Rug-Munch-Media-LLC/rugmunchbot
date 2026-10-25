"""Render one queued webhook alert item as Telegram HTML.

``alert_worker`` owns delivery; this module owns what a delivered alert looks
like. Every branch is total: an unknown ``type`` or a garbage payload renders a
fallback card instead of raising, because a formatting error must never cost a
delivery retry.
"""

from __future__ import annotations

from typing import Any

from rugmunchbot.config import fmt_number
from rugmunchbot.formatters import footer_links, sep, short_addr


def _fmt_scan_line(addr: str) -> str:
    addr = (addr or "").lower()
    if not addr:
        return ""
    return f"\U0001f50d <b>Scan:</b> /scan <code>{addr}</code>"


def _fmt_chain_short(chain: str) -> str:
    return {
        "ethereum": "ETH",
        "solana": "SOL",
        "bsc": "BSC",
        "polygon": "MATIC",
        "base": "BASE",
    }.get((chain or "").lower(), (chain or "unknown").upper())


def _fmt_money(value: Any) -> str:
    """Format a USD value — ``fmt_number`` already includes the ``$`` sign."""
    try:
        return fmt_number(float(value or 0))
    except (ValueError, TypeError):
        return "$0"


def format_alert(item: dict[str, Any]) -> str:
    """Build the HTML alert text for a queued event (provider payload)."""
    etype = str(item.get("type") or "unknown")
    payload = item.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}

    token_addr = str(payload.get("token_address") or payload.get("address") or "").lower()
    symbol = str(payload.get("token_symbol") or payload.get("symbol") or "").upper() or (
        short_addr(token_addr) if token_addr else "???"
    )
    chain = str(payload.get("chain") or "")

    header = f"{sep()}\n\n"

    if etype == "token.whale_move":
        value = _fmt_money(payload.get("value_decimal"))
        text = (
            f"\U0001f40b <b>Whale Move!</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"\U0001f4b0 <b>Value:</b> {value}\n"
            f"\U0001f464 <b>From:</b> <code>{payload.get('from_address', '')}</code>\n"
            f"\U0001f3e6 <b>To:</b> <code>{payload.get('to_address', '')}</code>\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.price_change":
        direction = str(payload.get("direction") or "")
        arrow = (
            "\U0001f4c8"
            if direction == "up"
            else "\U0001f4c9"
            if direction == "down"
            else "\U0001f4ca"
        )
        change = str(payload.get("change_pct") or "0")
        price = _fmt_money(payload.get("price"))
        text = (
            f"{arrow} <b>Price Alert: {symbol}</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"\U0001f4ca <b>Change:</b> {change}% ({direction or 'move'})\n"
            f"\U0001f4b5 <b>Price:</b> {price}\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.liquidity_change":
        value = _fmt_money(payload.get("value_decimal"))
        method = str(payload.get("detection_method") or "")
        text = (
            f"\U0001f4a7 <b>Liquidity Change: {symbol}</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"\U0001f4b0 <b>Value:</b> {value}\n"
            + (f"\U0001f4cb <b>Source:</b> {method}\n" if method else "")
            + f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.deployed":
        name = str(payload.get("token_name") or "")
        deployer = payload.get("deployer_address", "")
        text = (
            f"\U0001f195 <b>New Token Launched</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n"
            + (f"\U0001f3f7\ufe0f <b>Name:</b> {name}\n" if name else "")
            + (f"\U0001f464 <b>Deployer:</b> <code>{deployer}</code>\n" if deployer else "")
            + f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.first_swap":
        value = _fmt_money(payload.get("value_decimal"))
        text = (
            f"\U0001f504 <b>First Swap: {symbol}</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"\U0001f4b0 <b>Value:</b> {value}\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "wallet.activity":
        value = _fmt_money(payload.get("value_decimal"))
        text = (
            f"\U0001f45b <b>Wallet Activity</b>\n{header}"
            f"\U0001f464 <b>From:</b> <code>{payload.get('from_address', '')}</code>\n"
            f"\U0001f3e6 <b>To:</b> <code>{payload.get('to_address', '')}</code>\n"
            f"\U0001f4b0 <b>Value:</b> {value}\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    elif etype == "token.monitor_change":
        changes = payload.get("changes") or []
        changes_text = (
            "\n".join(f"\u2022 {c}" for c in changes) if changes else "Security properties changed."
        )
        text = (
            f"\U0001f6f0\ufe0f <b>Monitor Change: {symbol}</b>\n{header}"
            f"<b>{symbol}</b> <code>{token_addr}</code>\n\n"
            f"{changes_text}\n"
            f"\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"
        )
    else:
        text = f"\U0001f514 <b>Alert: {symbol}</b>\n{header}\U0001f4c1 <b>Chain:</b> {_fmt_chain_short(chain)}\n"

    scan_line = _fmt_scan_line(token_addr)
    text += f"{scan_line}\n" if scan_line else ""
    text += footer_links()
    return text
