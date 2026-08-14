#!/usr/bin/env python3
"""
RugMunchBot -- Payment handlers (BillingProvider seam, no-op in OSS).

Telegram Stars pre-checkout and successful-payment routing. The live bot
credits Stars balances, applies top-up packs and activates paid tiers
(config.TIERS/TOP_UP_PACKS/BUY_STARS_PACKS — private revenue logic). OSS
ships WITHOUT Stars monetization: handlers acknowledge payments without
mutating balances or tiers. Operators plug in their own monetization via
``plugins.billing.BillingProvider`` (see ``get_billing_provider()``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rugmunchbot import db
from rugmunchbot.plugins.billing import get_billing_provider
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot")


async def precheckout(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer Telegram's pre-checkout query for a Stars invoice.

    Kept faithful to the live handler: only known invoice prefixes are
    accepted. No invoice can be initiated in the default OSS build (billing
    packs are empty), so this is effectively a no-op until an operator
    configures a BillingProvider.
    """
    query = update.pre_checkout_query
    if query is None:
        return
    payload = query.invoice_payload  # type: ignore[union-attr]  # PTB None-able fields
    valid_prefixes = ("buystars_", "topup_", "sub_", "deep_")
    if any(payload.startswith(p) for p in valid_prefixes):
        await query.answer(ok=True)  # type: ignore[union-attr]  # PTB None-able fields
    else:
        logger.warning("Unknown invoice payload: %s", payload)
        await query.answer(ok=False, error_message="Invalid invoice.")  # type: ignore[union-attr]  # PTB None-able fields


async def successful_payment(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Acknowledge a successful Stars payment WITHOUT crediting balances/tiers.

    Live behavior: credits Stars, applies top-up packs, activates paid tiers
    and runs paid deep scans — all private revenue logic. OSS no-ops through
    the active BillingProvider: the user is registered, the event is logged,
    and a neutral confirmation is sent. Nothing is charged or granted.
    # TODO(port): wire quota grants / tier activation via BillingProvider if
    the operator supplies one.
    """
    provider = get_billing_provider()
    user = update.effective_user
    message = update.message
    if user is None or message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)
    logger.info(
        "successful_payment_received",
        user_id=user.id,
        provider=type(provider).__name__,
    )
    await message.reply_text("\u2705 Payment received.", parse_mode="HTML")
