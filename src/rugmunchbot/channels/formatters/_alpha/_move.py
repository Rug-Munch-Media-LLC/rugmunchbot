"""Whale-move rendering for the alpha digest.

Pure rendering only. Split out of ``_alpha.py`` (2026-10-23).
"""

from __future__ import annotations

import html
from typing import Any

from rugmunchbot.channels.formatters._alerts import _item_links
from rugmunchbot.channels.formatters._alpha._lines import (
    _fmt_move_time,
    _fmt_usd,
    _tx_url,
)
from rugmunchbot.channels.formatters._primitives import (
    _ALPHA_TAKEAWAY_CHARS,
    _esc,
    _takeaway,
)


def _whale_move_lines(move: dict[str, Any]) -> list[str] | None:
    """The whale-move lines for the alpha digest, with the ACTUAL signal.

    Whale-signal gate: a whale move with NO wallet address (from/to) on
    either end is DROPPED (``None``) — a whale alert without the wallet is
    worthless noise. Every rendered move carries: wallet route (full,
    copy-able addresses), amount (USD + token), token + chain, a timestamp,
    and a scan/chart/tx source link. Pure — never raises.
    """
    frm = str(move.get("from_address") or "").strip()
    to = str(move.get("to_address") or "").strip()
    if not (frm or to):
        return None
    title = _esc(move.get("title") or "Whale move")
    chain = _esc(move.get("chain") or "solana")
    lines = [f"\u2022 {title} ({chain})"]

    # Amount + token (USD value first, then token symbol).
    usd = _fmt_usd(move.get("value_usd"))
    token = str(move.get("token_symbol") or "").strip()
    bits: list[str] = []
    if usd:
        bits.append(usd)
    if token and token not in ("?", "unknown"):
        bits.append(_esc(token))
    if bits:
        lines.append("   \U0001f4b0 " + " ".join(bits))

    # Wallet route (full addresses — the actual wallet signal).
    if frm and to:
        lines.append(f"   \U0001f45b <code>{_esc(frm)}</code> → <code>{_esc(to)}</code>")
    elif to:
        lines.append(f"   \U0001f45b → <code>{_esc(to)}</code>")
    elif frm:
        lines.append(f"   \U0001f45b <code>{_esc(frm)}</code> →")

    body = _esc(_takeaway(move.get("body") or move.get("summary"), _ALPHA_TAKEAWAY_CHARS))
    if body:
        lines.append(f"   \U0001f4ac {body}")

    addr = str(move.get("token_address") or "")
    links = ""
    if addr:
        lines.append(f"   \U0001f916 /scan <code>{_esc(addr)}</code>")
        links = _item_links(move, addr=addr, chain=str(move.get("chain") or "solana"))
    tx = str(move.get("transaction_hash") or "").strip()
    if tx:
        tx_link = _tx_url(str(move.get("chain") or ""), tx)
        if tx_link:
            tx_bit = f'<a href="{html.escape(tx_link, quote=True)}">Tx</a>'
            links = f"{links} · {tx_bit}" if links else tx_bit
    if links:
        lines.append(f"   {links}")

    ts = _fmt_move_time(move.get("timestamp"))
    if ts:
        lines.append(f"   {ts}")
    return lines


def render_whale_moves(whale_moves: list[dict[str, Any]]) -> list[str]:
    """Rendered whale lines (wallet-less moves dropped), no section header."""
    rendered: list[str] = []
    for move in whale_moves[:4]:
        move_lines = _whale_move_lines(move)
        if move_lines:
            rendered.extend(move_lines)
    return rendered
