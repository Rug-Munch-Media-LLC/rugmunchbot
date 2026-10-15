"""Photo -> hosted vision lane (Groq Qwen3.8-27b, Gemini flash fallback).

Sends the photo (data URI) through the SAME hosted ladder as text chat
(llm_config.chat_with_fallback, tier="snappy") and replies with the model's
read — charts, contract screenshots, token images, wallet/tx views. One lane,
no second setup: the photo path rides the same Groq -> Gemini -> ... chain.
Groups require the bot to be addressed (same etiquette as text); owner is
exempt. Fail-open: never raises.
"""

from __future__ import annotations

import base64
import time
from html import escape
from typing import TYPE_CHECKING

from telegram.constants import ParseMode
from telegram.error import TelegramError

from rugmunchbot import db
from rugmunchbot.db._base import run_db
from rugmunchbot.formatters import is_owner
from rugmunchbot.handlers.chat_ai._markup import _markdown_to_html
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


logger = get_logger(__name__)

# 2026-10-15 (later): vision rides the hosted lane via chat_with_fallback —
# Groq Qwen3.8-27b (vision verified on a real image, HTTP 200) with the Gemini
# flash fallback. The local :8014 mmproj lane is retired from the product path.
MAX_REPLY_CHARS = 3900

_VISION_PROMPT = (
    "You are Rug Munch Bot, a crypto analyst. Read this image for the user. "
    "If it's a chart: structure, trend, key levels, what stands out. "
    "If it's a contract or token page: extract the identifiers and flag risks. "
    "If it's a wallet or transaction view: summarize amounts, counterparties, anything off. "
    "Otherwise: describe what matters. Telegram Markdown, tight, no financial advice."
)


async def photo_chat_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.message
    if user is None or message is None or not message.photo:
        return

    # Same etiquette as text: groups only when addressed; owner exempt.
    if not is_owner(user.id):
        try:
            from rugmunchbot.handlers.chat_ai._routing._handler import _is_bot_addressed

            bot_username = getattr(getattr(ctx, "bot", None), "username", None)
            if not _is_bot_addressed(update, message, bot_username=bot_username):
                return
        except Exception:
            logger.debug("vision: addressing check failed", exc_info=True)

    caption = (message.caption or "").strip()
    try:
        photo = message.photo[-1]
        tg_file = await photo.get_file()
        raw = bytes(await tg_file.download_as_bytearray())
    except (TelegramError, ConnectionError, OSError, TimeoutError):
        logger.debug("vision: photo download failed", exc_info=True)
        return

    data_uri = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    prompt = _VISION_PROMPT + (f"\n\nUser caption: {caption}" if caption else "")

    t0 = time.monotonic()
    answer = ""
    try:
        from rugmunchbot.llm_config import chat_with_fallback

        answer = await chat_with_fallback(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            "snappy",
            900,
        )
        answer = (answer or "").strip()
    except Exception:
        logger.exception("vision: lane call failed (user=%s)", user.id)

    if not answer:
        answer = "👁️ I couldn't read that image right now — try again in a moment."

    html = _markdown_to_html(answer)
    if len(html) > MAX_REPLY_CHARS:
        html = html[:MAX_REPLY_CHARS].rstrip() + "…"
    try:
        await message.reply_text(html, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except TelegramError:
        try:
            await message.reply_text(escape(answer)[:MAX_REPLY_CHARS])
        except TelegramError:
            logger.debug("vision: reply failed", exc_info=True)

    try:
        await run_db(db.increment_usage, user.id, ai_msg=True)
    except Exception:
        logger.debug("vision: usage increment failed", exc_info=True)
    logger.info(
        "vision trace: user=%s ok=%s latency_ms=%s chars=%s",
        user.id,
        bool(answer),
        int((time.monotonic() - t0) * 1000),
        len(answer),
    )
