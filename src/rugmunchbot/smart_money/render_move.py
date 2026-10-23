"""Render one smart-money move as a channel post."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rugmunchbot.channels.formatters._primitives import NewsPost

from ._fmt import esc, short, usd
from .render_base import compose

_RISK = (
    "copying a wallet is not a thesis — an entry can be a hedge, a transfer "
    "or an exit. Verify on-chain before acting. Not financial advice."
)


def render_move(feed: Any, move: dict[str, Any]) -> NewsPost:
    """Build the post for one normalised move (fields absent stay absent)."""
    symbol = esc(move.get("symbol") or "?")
    action = esc(move.get("action") or "move")
    headline = f"🧠 <b>SMART MONEY · {symbol} {action.upper()}</b>"

    evidence: list[str] = []
    if move.get("wallet"):
        evidence.append(
            f"Wallet <code>{esc(short(move['wallet']))}</code> · {esc(move.get('chain') or '?')}"
        )
    if move.get("entity"):
        evidence.append(f"Label: {esc(move['entity'])}")
    evidence.append(f"Size: <b>{usd(move.get('amount_usd'))}</b>")
    if move.get("pnl_usd") is not None:
        evidence.append(f"Wallet P&L: {usd(move.get('pnl_usd'))}")
    if move.get("token_address"):
        evidence.append(f"Token: <code>{esc(short(move['token_address']))}</code>")
    if move.get("tx_hash"):
        evidence.append(f"Tx: <code>{esc(short(move['tx_hash'], 8, 6))}</code>")

    return compose(
        headline=headline,
        evidence=evidence,
        source=(f"rmi {feed.source}".strip() if feed.source else "unknown"),
        updated=feed.updated_at,
        risk=_RISK,
    )
