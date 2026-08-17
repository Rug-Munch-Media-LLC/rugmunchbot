"""Chat routing — free-text entry gate + pipeline wiring.

``_is_bot_addressed`` implements the group-etiquette gate;
``ai_chat_handler`` is the single free-text pipeline entry (owner admin
console, kill-switch, injection block, $TOKEN card race, structural
routing, shared AI core); ``register_chat_ai`` is the one-line wiring.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from telegram import MessageEntity, Update

from app.domains.telegram.rugmunchbot import anti_abuse, db
from app.domains.telegram.rugmunchbot.admin_state import track_message
from app.domains.telegram.rugmunchbot.db._base import run_db
from app.domains.telegram.rugmunchbot.formatters import TOKEN_RE, is_owner
from app.domains.telegram.rugmunchbot.handlers.chat_ai._admin import (
    _ADMIN_BUTTON_MAP,
    _dispatch_admin_button,
    _ensure_admin_reply_kb,
)
from app.domains.telegram.rugmunchbot.handlers.chat_ai._guards import _injection_warning
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._core import chat_ai_response
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._structural import _route_structural
from app.domains.telegram.rugmunchbot.handlers.chat_ai._routing._token_card import (
    _lookup_token_symbol,
    _TokenCardFlag,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Message
    from telegram.ext import Application, ContextTypes

logger = get_logger("rmi_bot.chat_ai")


def _is_bot_addressed(
    update: Update,
    message: Message,
    bot_username: str | None = None,
) -> bool:
    """True when a free-text message is explicitly addressed to the bot.

    Group-etiquette gate for the free-text AI pipeline: un-@mentioned group
    chatter must never auto-trigger the AI (or the bare-address scan /
    $TOKEN price card) and annoy the room, so group messages only fire when
    the bot is @mentioned or replied to.

      * private DMs — always addressed (the user opened a 1:1 chat);
      * group / supergroup — addressed only when the message @mentions the
        bot (``@RugMunchBot ...``) or replies to one of the bot's messages;
      * channels / anything else — never addressed.

    ``bot_username`` is optional: normally resolved from the update's bot
    (populated by ``get_me()`` at startup, see bot.setup_bot_profile); pass
    it explicitly in tests or when the update carries no bot instance. When
    no username can be resolved the mention match fails closed (group
    mentions are ignored rather than firing on any @handle).
    """
    chat = update.effective_chat
    if chat is None:
        return False
    if chat.type == "private":
        return True
    if chat.type not in ("group", "supergroup"):
        return False

    # A reply to the bot's own message counts as addressing it.
    reply = getattr(message, "reply_to_message", None)
    reply_user = getattr(reply, "from_user", None) if reply is not None else None
    if reply_user is not None and reply_user.is_bot:
        return True

    # @mention of the bot ("@RugMunchBot is this a honeypot?").
    if not bot_username:
        get_bot = getattr(update, "get_bot", None)
        if callable(get_bot):
            with contextlib.suppress(Exception):
                bot_username = getattr(get_bot(), "username", None)
    if not bot_username:
        return False
    text = message.text or ""
    for ent in getattr(message, "entities", None) or ():
        if getattr(ent, "type", None) != MessageEntity.MENTION:
            continue
        handle = text[ent.offset : ent.offset + ent.length].lstrip("@").lower()
        if handle == bot_username.lower():
            return True
    return False


async def ai_chat_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Single free-text pipeline entry (MessageHandler).

    Merged from the old ``handlers.message.handle_message`` (owner admin
    console, bot kill-switch, known-scam block, bare-address auto-scan,
    $TOKEN price card) and the backend RAG chat core (:func:`chat_ai_response`).
    """
    user = update.effective_user
    message = update.message
    if user is None or message is None or not message.text:
        return
    text = message.text.strip()
    if not text:
        return

    # Group-etiquette gate: the free-text pipeline only fires when the bot
    # is explicitly addressed — private DMs always count; group messages
    # only when the bot is @mentioned or replied to (see
    # :func:`_is_bot_addressed`). Un-@mentioned group chatter returns
    # silently (no AI, no scans, no $TOKEN cards). /chat and /ask are
    # separate CommandHandlers and bypass this gate entirely.
    # bot_username is resolved from ctx.bot first (populated by get_me() at
    # startup), falling back to the update's attached bot inside the helper.
    ctx_bot_username = getattr(getattr(ctx, "bot", None), "username", None)
    if not _is_bot_addressed(update, message, bot_username=ctx_bot_username):
        logger.debug("free-text not addressed to bot — ignored (chat=%s)", getattr(update.effective_chat, "type", "?"))
        return

    u = await run_db(db.get_or_create_user, user.id, user.username, user.first_name)
    if u.get("is_banned"):
        return
    # NOTE: the per-user spam gate lives in chat_ai_response (single
    # check_spam call). Calling it HERE too was the P0 bug: the first call
    # STAMPS _user_last_action[user.id] = now (states.check_spam), then the
    # second call milliseconds later sees now - last < 3 → True → every
    # non-owner free-text reply was silently dropped.
    await run_db(db.record_activity, msg=True)

    # Owner admin console (v7): in private chats the owner gets a persistent
    # admin reply keyboard + one-tap routing for its buttons. This runs
    # BEFORE the bot kill-switch so the owner can still reach /admin toggles
    # during maintenance.
    if is_owner(user.id) and update.effective_chat and update.effective_chat.type == "private":
        await _ensure_admin_reply_kb(update)
        if text in _ADMIN_BUTTON_MAP:
            await _dispatch_admin_button(update, ctx, text)
            return

    # Admin kill-switch: whole bot disabled (synced from the admin API).
    if await run_db(db.get_bot_state, "bot_enabled", "on") != "on":
        return

    # Analytics: count every inbound message handled (Redis, for the admin API).
    try:
        await track_message()
    except Exception:
        logger.debug("admin_state track_message failed", exc_info=True)

    # v6 anti-abuse: prompt injection + known scam addresses. Injection is
    # blocked BEFORE feature routing so an injected address is never scanned.
    injection = anti_abuse.check_prompt_injection(text)
    if injection:
        logger.info("prompt-injection blocked user=%s category=%s", user.id, injection)
        await _injection_warning(update, injection)
        return

    # $TOKEN price card: fire the DexScreener lookup CONCURRENTLY with the AI
    # pipeline instead of serializing a 10s HTTP call before it (the old
    # behavior delayed every "$TOKEN" message by up to 10s). The card wins if
    # it lands before the AI answer is ready (chat_ai_response decides);
    # structural routing cancels it when the message was already handled.
    # token_card_flag is the shared card-sent signal (P2 race fix) that lets
    # the AI pipeline detect a card landing just after the decide-wait and
    # never double-send card + AI answer.
    token_card_flag = _TokenCardFlag() if TOKEN_RE.search(text) else None
    token_lookup_task = (
        asyncio.create_task(_lookup_token_symbol(text, message, sent_flag=token_card_flag))
        if token_card_flag is not None
        else None
    )

    # Feature routing that does NOT consume the AI quota: known-scam block,
    # bare-address auto-scan. The $TOKEN lookup runs concurrently (above), so
    # _route_structural skips it here.
    try:
        if await _route_structural(update, ctx, text, user, skip_token_lookup=True):
            if token_lookup_task is not None:
                token_lookup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await token_lookup_task
            return

        # Free text → shared AI core (quota + flood + backend RAG chat). The
        # core fails open on its own; anything escaping here is structural
        # routing (admin console / scan / $TOKEN lookup) — log it and cancel
        # the concurrent lookup so no late price card or dangling task
        # survives the failed turn (single-turn isolation for the service).
        await chat_ai_response(
            update, ctx, text, user, token_lookup_task=token_lookup_task, token_card_flag=token_card_flag
        )
    except Exception:
        logger.exception("ai_chat_handler error (user=%s) text=%.60s", user.id, text)
        if token_lookup_task is not None and not token_lookup_task.done():
            token_lookup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await token_lookup_task


def register_chat_ai(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register the free-text pipeline (MessageHandler) on the bot Application.

    One-line wiring for bot.py — this is the ONLY free-text handler:

        from app.domains.telegram.rugmunchbot.handlers.chat_ai import register_chat_ai
        register_chat_ai(app)

    WIRING NOTE: python-telegram-bot dispatches to the FIRST matching handler,
    so no other ``MessageHandler(filters.TEXT & ~filters.COMMAND)`` may be
    registered alongside this one (the old ``handle_message`` registration
    was removed when the pipelines were merged).

    GROUP ETIQUETTE: ``ai_chat_handler`` gates itself with
    :func:`_is_bot_addressed` — free text fires in private DMs always, and in
    groups only when the bot is @mentioned or replied to. Un-@mentioned
    group messages are dropped before any AI/scan/$TOKEN work. /chat and
    /ask are explicit CommandHandlers and always work.
    """
    from telegram.ext import MessageHandler, filters

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_chat_handler))
