"""Buy-popup commands: /buybot, /buybot_on, /buybot_off, /buybot_preview, /buybot_template.

Every mutation is admin-gated through :func:`community.auth.require_chat_admin`
and validated by the ``community`` models, so ``/setup`` and ``/buybot`` can
never disagree about the token, the presets or the emoji.
"""

from __future__ import annotations

from html import escape
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from rugmunchbot.buybot.callbacks import CALLBACK_PATTERN, on_buy_callback
from rugmunchbot.buybot.commands import USAGE, apply_intent, parse
from rugmunchbot.buybot.models import CARD_BUTTONS, CARD_FIELDS, BuybotConfig
from rugmunchbot.buybot.render import BuyEvent, button_rows, render_card
from rugmunchbot.buybot.state import get_state
from rugmunchbot.buybot.store import bot_client, load_config, save_config
from rugmunchbot.community.auth import require_chat_admin
from rugmunchbot.support.logging import get_logger

__all__ = ["SETUP_HINT", "TEMPLATE_TEXT", "register_buybot", "status_card"]

logger = get_logger("rmi_bot.buybot.handlers")
SETUP_HINT = (
    "🛠 <b>Buy popup</b> — not configured yet.\n"
    "1. <code>/buybot token &lt;chain&gt; &lt;address&gt; [symbol]</code>\n"
    "2. <code>/buybot presets 0.1,0.5,1</code>\n"
    "3. <code>/buybot_on</code> — start posting buy cards in this chat"
)
TEMPLATE_TEXT = (
    "🧩 <b>Card template</b>\n"
    f"rows, fixed order: {', '.join(CARD_FIELDS)}\n"
    f"action buttons: {', '.join(CARD_BUTTONS)}\n"
    "Narrow them with /buybot fields … and /buybot buttons …"
)


def status_card(config: BuybotConfig) -> str:
    """Admin-facing summary, or the setup card when no token is configured."""
    if config.token is None:
        return SETUP_HINT
    presets = ", ".join(f"{preset:g}" for preset in config.presets) or "—"
    return (
        f"🛒 <b>Buy popup</b> — {'ON' if config.enabled else 'OFF'}\n"
        f"token: <b>{escape(config.token.symbol)}</b> on {escape(config.chain.value)}\n"
        f"<code>{escape(config.token.address)}</code>\n"
        f"presets: {presets}\n"
        f"min buy: ${config.min_buy_usd:,.2f} · dedupe {config.dedupe_window_s}s · "
        f"cap {config.max_cards_per_min}/min\n"
        f"rows: {', '.join(config.fields)}\n"
        "Change: /buybot token|presets|emoji|min|fields|buttons|dedupe|cap"
    )


def _reason(error: ValueError) -> str:
    """First, human-readable line of a validation error."""
    errors = getattr(error, "errors", None)
    if callable(errors):
        try:
            return str(errors()[0].get("msg", error)).removeprefix("Value error, ")
        except (IndexError, KeyError, TypeError):
            return str(error)
    return str(error)


async def _send(update: Update, text: str, keyboard: InlineKeyboardMarkup | None = None) -> None:
    await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True
    )


def _keyboard(config: BuybotConfig, chat_id: int) -> InlineKeyboardMarkup | None:
    rows = button_rows(config, chat_id)
    if not rows:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(label, callback_data=data) for label, data in row] for row in rows]
    )


async def _gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """Return the chat id when the caller may administer it, else deny."""
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return None
    allowed, denial = await require_chat_admin(chat.id, user.id, bot=context.bot)
    if not allowed:
        await _send(update, escape(denial or "Admin only."))
        return None
    return chat.id


async def _load(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> BuybotConfig:
    return await load_config(chat_id, bot_client(context))


async def _persist(
    update: Update, context: ContextTypes.DEFAULT_TYPE, config: BuybotConfig
) -> bool:
    ok, reason = await save_config(config, bot_client(context))
    if not ok:
        await _send(update, f"⚠️ {escape(reason or 'save failed')}")
    return ok


async def cmd_buybot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = await _gate(update, context)
    if chat_id is None:
        return
    config = await _load(context, chat_id)
    try:
        intent = parse(context.args or [])
        if intent is None:
            await _send(update, status_card(config))
            return
        updated = apply_intent(config, intent)
    except ValueError as error:
        await _send(update, f"⚠️ {escape(_reason(error))}")
        return
    if await _persist(update, context, updated):
        await _send(update, status_card(updated))


async def cmd_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = await _gate(update, context)
    if chat_id is None:
        return
    config = await _load(context, chat_id)
    if config.token is None:
        await _send(update, SETUP_HINT)
        return
    updated = config.model_copy(update={"enabled": True})
    if await _persist(update, context, updated):
        await _send(update, f"✅ Buy popup ON for <b>{escape(config.token.symbol)}</b>.")


async def cmd_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = await _gate(update, context)
    if chat_id is None:
        return
    config = await _load(context, chat_id)
    updated = config.model_copy(update={"enabled": False})
    if await _persist(update, context, updated):
        await _send(update, "⏸ Buy popup OFF — the config and drop counter were kept.")


async def cmd_preview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = await _gate(update, context)
    if chat_id is None:
        return
    config = await _load(context, chat_id)
    if config.token is None:
        await _send(update, SETUP_HINT)
        return
    try:
        amount = float(context.args[0]) if context.args else config.presets[0]
    except (IndexError, TypeError, ValueError):
        await _send(update, f"⚠️ {escape(USAGE['presets'])}")
        return
    if amount <= 0:
        await _send(update, f"⚠️ {escape(USAGE['presets'])}")
        return
    market = await get_state(context).snapshot(config)
    price = market.price_usd if market is not None else None
    event = BuyEvent(
        buyer=getattr(update.effective_user, "first_name", "") or "preview",
        usd_value=amount * price if price else None,
        amount_tokens=amount,
    )
    card = render_card(config, event, market, note="preview — no buy happened, no key was touched")
    await _send(update, card, _keyboard(config, chat_id))


async def cmd_template(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = await _gate(update, context)
    if chat_id is None:
        return
    await _send(update, TEMPLATE_TEXT)


def register_buybot(app: Any) -> None:
    """Register the five commands and the popup's callback router."""
    app.add_handler(CommandHandler("buybot", cmd_buybot, block=False))
    app.add_handler(CommandHandler("buybot_on", cmd_on, block=False))
    app.add_handler(CommandHandler("buybot_off", cmd_off, block=False))
    app.add_handler(CommandHandler("buybot_preview", cmd_preview, block=False))
    app.add_handler(CommandHandler("buybot_template", cmd_template, block=False))
    app.add_handler(CallbackQueryHandler(on_buy_callback, pattern=CALLBACK_PATTERN))
