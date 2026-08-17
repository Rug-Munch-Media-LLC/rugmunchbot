"""Chat routing — the $TOKEN instant price card (race-safe).

``_lookup_token_symbol`` sends an instant DexScreener price card for a
``$TOKEN`` mention; ``_TokenCardFlag`` is the shared card-sent signal;
``_token_card_won`` / ``_cancel_token_lookup`` decide the race between
the card and the AI answer. ``TOKEN_CARD_DECIDE_WAIT`` resolves through
the package so tests can patch ``chat_ai._routing.TOKEN_CARD_DECIDE_WAIT``
(monkeypatch contract).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot.config import DEXSCREENER, fmt_chain, fmt_number, fmt_pct
from app.domains.telegram.rugmunchbot.formatters import TOKEN_RE, footer_links, web_scan_button
from app.domains.telegram.rugmunchbot.handlers.chat_ai import _routing as _routing_mod
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Message

logger = get_logger("rmi_bot.chat_ai")

# ══════════════════════════════════════════════
# $TOKEN SYMBOL LOOKUP (ported from handlers/message.py)
# ══════════════════════════════════════════════


class _TokenCardFlag:
    """Shared mutable holder for the ``$TOKEN`` card-sent signal (P2 fix).

    ``_lookup_token_symbol`` sets ``sent = True`` the moment a price card is
    delivered. The AI pipeline checks the flag right before sending any
    reply, so a card that lands inside the ``_token_card_won`` decide-wait
    (or just after its timeout raced with ``_cancel_token_lookup``) can never
    double-send with the AI answer.
    """

    __slots__ = ("sent",)

    def __init__(self) -> None:
        self.sent = False


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Best-effort numeric coercion for DexScreener payload fields (P2 fix).

    DexScreener can return ``None``, absent or non-numeric values (e.g.
    ``"N/A"``) for price / volume / market cap — a raw ``float(...)`` would
    crash the ``$TOKEN`` card. Fail-open to ``default`` on any malformed
    input.
    """
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def _lookup_token_symbol(text: str, message: Message, sent_flag: _TokenCardFlag | None = None) -> bool:
    """Instant ``$TOKEN`` price card via DexScreener.

    Returns True when a price card was sent; False when there is no symbol
    or the lookup failed so the caller falls through to the AI pipeline
    (matches the old handle_message behavior).

    ``sent_flag``: optional shared holder (see :class:`_TokenCardFlag`);
    ``sent`` is set True the moment a card is delivered so the AI pipeline
    can detect a card that landed inside the decide-wait / after its
    timeout and never double-send (P2 race fix).
    """
    token_match = TOKEN_RE.search(text)
    if not token_match:
        return False
    symbol = token_match.group(1).upper()
    with contextlib.suppress(Exception):
        await message.chat.send_action("typing")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{DEXSCREENER}/search/?q={symbol}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                if pairs:
                    p = pairs[0]
                    addr = p.get("baseToken", {}).get("address", "")
                    chain = p.get("chainId", "")
                    price = _safe_float(p.get("priceUsd", 0))
                    h24 = p.get("priceChange", {}).get("h24") or 0
                    vol = _safe_float(p.get("volume", {}).get("h24") or 0)
                    mcap = _safe_float(p.get("marketCap") or 0)
                    name = p.get("baseToken", {}).get("name", symbol)
                    quick_text = (
                        f"\U0001fa99 <b>{name}</b> ({symbol}) {fmt_chain(chain)}\n"
                        f"<code>{addr}</code>\n\n"
                        f"\U0001f4b0 {fmt_number(price)} | \U0001f504 24h: {fmt_pct(h24)}\n"
                        f"\U0001f4c8 MCap: {fmt_number(mcap)} | \U0001f4a7 Vol: {fmt_number(vol)}"
                        f"{footer_links()}"
                    )
                    if addr:
                        await message.reply_text(
                            quick_text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=InlineKeyboardMarkup(
                                [
                                    [
                                        InlineKeyboardButton(
                                            "\U0001f50d Full Scan", callback_data=f"scan_{addr}_{chain}"
                                        )
                                    ],
                                    [web_scan_button(addr, chain)],
                                ]
                            ),
                            disable_web_page_preview=True,
                        )
                        if sent_flag is not None:
                            sent_flag.sent = True
                        return True
    except (
        ConnectionError,
        OSError,
        TimeoutError,
        httpx.HTTPError,
        httpx.TimeoutException,
        TelegramError,
    ) as e:
        logger.debug("chat_ai $TOKEN lookup failed for %s: %s", symbol, e)
    return False


async def _token_card_won(token_lookup_task: asyncio.Task[bool] | None) -> bool:
    """True when the concurrent ``$TOKEN`` DexScreener lookup sent a price card.

    Called only after the AI answer is ready. Waits at most
    ``TOKEN_CARD_DECIDE_WAIT`` for the lookup so a slow DexScreener never
    stalls the AI answer; an already-finished lookup returns instantly. The
    caller decides from the result: a landed price card IS the answer, so the
    AI answer is skipped. Fail-open — a broken lookup never blocks chat.
    """
    if token_lookup_task is None:
        return False
    try:
        return bool(
            await asyncio.wait_for(asyncio.shield(token_lookup_task), timeout=_routing_mod.TOKEN_CARD_DECIDE_WAIT)
        )
    except Exception:
        # Timeout or unexpected failure: the card loses the race to the AI
        # answer. (CancelledError is a BaseException and propagates — this
        # coroutine was cancelled by its caller and must unwind cleanly.)
        return False


async def _cancel_token_lookup(token_lookup_task: asyncio.Task[bool] | None) -> None:
    """Cancel the concurrent ``$TOKEN`` lookup when another path already replied.

    Keeps the lookup from firing a late price card after a canned greeting /
    guard reply, and prevents a dangling task on the early-return paths.
    """
    if token_lookup_task is not None and not token_lookup_task.done():
        token_lookup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await token_lookup_task
