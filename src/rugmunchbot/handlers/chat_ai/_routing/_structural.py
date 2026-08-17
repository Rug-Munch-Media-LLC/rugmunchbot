"""Chat routing — free-text feature routing (NO AI quota).

``_route_structural`` handles the known-scam block, bare-address auto-scan
and the ``$TOKEN`` instant price card before the AI pipeline. ``is_evm`` /
``is_sol`` resolve through the chat_ai package (monkeypatch contract).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import app.domains.telegram.rugmunchbot.handlers.chat_ai as chat_ai
from app.domains.telegram.rugmunchbot import anti_abuse
from app.domains.telegram.rugmunchbot.handlers.chat_ai._classify import _extract_addresses
from app.domains.telegram.rugmunchbot.handlers.chat_ai._guards import _scam_address_warning
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._token_card import _lookup_token_symbol
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.chat_ai")

# ══════════════════════════════════════════════
# FEATURE ROUTING (free text only, NO AI quota)
# ══════════════════════════════════════════════


async def _route_structural(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    text: str,
    user: Any,
    *,
    skip_token_lookup: bool = False,
) -> bool:
    """Free-text feature routing that does NOT consume the AI quota.

    Ported from the old ``handlers/message.py handle_message`` so features
    survive the dedup: known-scam block, bare-address auto-scan, and the
    ``$TOKEN`` instant price card. Returns True when the message was fully
    handled (a reply was sent) and the caller should stop.

    ``skip_token_lookup``: :func:`ai_chat_handler` fires the ``$TOKEN``
    DexScreener lookup CONCURRENTLY with the AI pipeline (so a slow
    DexScreener never serializes 10s before chat) and passes this flag so the
    lookup is not run twice. Direct callers keep the synchronous behavior.

    The old natural-language-address-extraction block is NOT ported: it was
    dead code (EVM_RE/SOL_RE are whole-string anchored, so it could never
    fire after the bare-address check) and embedded addresses in questions
    are now handled better by :func:`chat_ai_response` address enhancement
    (risk summary prepended to the AI prompt).
    """
    message = update.message
    if message is None:
        return False

    # 1. Known scam addresses are blocked before any scan or AI call.
    #    Uses _extract_addresses (unanchored) so embedded scam addresses are
    #    caught too — strictly more protective than the old anchored check.
    for addr in _extract_addresses(text):
        if anti_abuse.is_known_scam_address(addr):
            logger.info("known-scam address blocked user=%s addr=%.16s", user.id, addr)
            await _scam_address_warning(update, addr)
            return True

    # 2. Bare contract address (the whole message is the address) → full /scan.
    if chat_ai.is_evm(text) or chat_ai.is_sol(text):
        ctx.args = [text]
        from app.domains.telegram.rugmunchbot.handlers.scanning import cmd_scan

        await cmd_scan(update, ctx)
        return True

    # 3. $TOKEN symbol lookup → instant DexScreener price card.
    #    ai_chat_handler fires this CONCURRENTLY with the AI pipeline
    #    (skip_token_lookup=True) so a slow DexScreener never blocks chat.
    if skip_token_lookup:
        return False
    return bool(await _lookup_token_symbol(text, message))
