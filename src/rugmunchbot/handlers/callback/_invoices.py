#!/usr/bin/env python3
"""
RugMunchBot -- callback invoice branches.

Telegram Stars invoice callbacks (``buystars_``, ``deep_``, ``monitor_``,
``sub_``, ``topup_``). Split out of the historic single-function
``handle_callback`` chain (2026-08-16). The five near-identical
``send_invoice`` blocks now share ``_send_stars_invoice`` (DRY).
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import httpx
from telegram import LabeledPrice

from app.domains.telegram.rugmunchbot.config import BUY_STARS_PACKS, SCAN_PACKS, TIERS, TOP_UP_PACKS
from app.domains.telegram.rugmunchbot.states import _pending_deep_scans, _pending_monitors
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def _send_stars_invoice(
    ctx: ContextTypes.DEFAULT_TYPE,
    query: Any,
    *,
    log_label: str,
    title: str,
    description: str,
    payload: str,
    prices: list[LabeledPrice],
) -> str:
    """Send a Telegram Stars invoice, fail-open on provider errors.

    Returns ``"sent"`` on success, ``"unavailable"`` when the callback
    message is gone (caller must abort), or ``"error"`` after a provider
    failure (caller may clean up any pending state).
    """
    msg = query.message
    if msg is None:
        await query.answer("Message unavailable.", show_alert=True)
        return "unavailable"
    try:
        await ctx.bot.send_invoice(
            chat_id=msg.chat.id,
            title=title,
            description=description,
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=prices,
        )
        await query.answer()
        return "sent"
    except (httpx.HTTPError, KeyError, TypeError) as e:
        logger.debug("unknown_error", exc_info=True)
        logger.error(f"{log_label} invoice error: {e}")
        await query.answer(
            "Could not create invoice. Ensure bot has Telegram Stars enabled in @BotFather.",
            show_alert=True,
        )
        return "error"


async def handle(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    query: Any,
    user: Any,
    data: str,
) -> bool:
    """Handle Stars invoice callbacks. True when a branch matched."""
    if data.startswith("buystars_"):
        pack_key = data[9:]
        pack = BUY_STARS_PACKS.get(pack_key)
        if not pack:
            await query.answer("Invalid pack.", show_alert=True)
            return True
        await _send_stars_invoice(
            ctx,
            query,
            log_label="Buy stars",
            title=str(pack["name"]),
            description=f"Purchase {pack['name']} for your RugMunch account.",
            payload=f"buystars_{pack_key}",
            prices=[LabeledPrice(str(pack["name"]), int(pack["stars"]))],
        )
    elif data.startswith("deep_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            await query.answer("Invalid request.", show_alert=True)
            return True
        addr, chain = parts[1], parts[2]
        token = secrets.token_hex(8)
        _pending_deep_scans[token] = {
            "user_id": user.id,
            "address": addr,
            "chain": chain,
            "chat_id": query.message.chat.id if query.message is not None else 0,
            "message_id": query.message.message_id if query.message is not None else 0,
        }
        status = await _send_stars_invoice(
            ctx,
            query,
            log_label="Deep scan",
            title="\U0001f52c Deep Token Scan",
            description=f"Complete forensic analysis of {addr} on {chain}.",
            payload=f"deep_{token}",
            prices=[LabeledPrice("Deep Scan", int(SCAN_PACKS["deep_scan"]["stars"]))],
        )
        if status == "error":
            _pending_deep_scans.pop(token, None)
    elif data.startswith("monitor_"):
        parts = data.split("_", 2)
        if len(parts) < 3:
            await query.answer("Invalid request.", show_alert=True)
            return True
        addr, chain = parts[1], parts[2]
        token = secrets.token_hex(8)
        _pending_monitors[token] = {
            "user_id": user.id,
            "address": addr,
            "chain": chain,
        }
        status = await _send_stars_invoice(
            ctx,
            query,
            log_label="Monitor",
            title="\U0001f6f0\ufe0f Token Monitor",
            description=f"30 days of live monitoring for {addr} on {chain}.",
            payload=f"mon_{token}",
            prices=[LabeledPrice("Token Monitor (30d)", int(SCAN_PACKS["monitor"]["stars"]))],
        )
        if status == "error":
            _pending_monitors.pop(token, None)
    elif data.startswith("sub_"):
        tier_key = data[4:]
        if tier_key == "free":
            await query.answer("Free tier is already active!", show_alert=True)
            return True
        sub_tier = TIERS.get(tier_key)
        if not sub_tier:
            await query.answer("Invalid tier.", show_alert=True)
            return True
        await _send_stars_invoice(
            ctx,
            query,
            log_label="Sub",
            title=f"{sub_tier['emoji']} {sub_tier['name']} - 30 Days",
            description=(
                f"Unlock {sub_tier['name']} tier for 30 days.\n"
                f"\u2022 {sub_tier['scans_per_week']} scans/week\n"
                f"\u2022 {sub_tier['ai_msgs_per_week']} AI messages/week\n"
                f"\u2022 All premium features"
            ),
            payload=f"sub_{tier_key}",
            prices=[LabeledPrice(f"{sub_tier['name']} - 30 days", sub_tier["price_stars"])],
        )
    elif data.startswith("topup_"):
        pack_key = data[6:]
        pack = TOP_UP_PACKS.get(pack_key)
        if not pack:
            await query.answer("Invalid pack.", show_alert=True)
            return True
        await _send_stars_invoice(
            ctx,
            query,
            log_label="Topup",
            title=str(pack["name"]),
            description=f"Purchase {pack['name']} \u2014 never expires!",
            payload=f"topup_{pack_key}",
            prices=[LabeledPrice(str(pack["name"]), int(pack["stars"]))],
        )
    else:
        return False
    return True
