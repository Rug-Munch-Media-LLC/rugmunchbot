"""Photo -> local vision lane (Qwythos-9B; llama.cpp :8014, the mmproj lane).

Sends the photo (data URI) to the local multimodal chat lane and replies with
the model's read — charts, contract screenshots, token images, wallet/tx views.
Zero API cost. Groups require the bot to be addressed (same etiquette as text);
owner is exempt. Fail-open: never raises.
"""

from __future__ import annotations

import base64
import time
from html import escape
from typing import TYPE_CHECKING

import httpx
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

# 2026-10-15: the vision lane is :8014, NOT the chat router on :8009.
# :8009 runs the chat/long presets with NO mmproj, so an image request there
# returned HTTP 500; :8014 (llama-heavy) loads the Qwythos mmproj and serves the
# image (verified HTTP 200). The old URL meant the photo handler had NEVER
# worked against the consolidated router.
VISION_URL = "http://127.0.0.1:8014/v1/chat/completions"
VISION_MODEL = "qwythos-9b"
VISION_TIMEOUT = 180.0
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
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=VISION_TIMEOUT, write=30.0, pool=5.0)
        ) as client:
            resp = await client.post(
                VISION_URL,
                json={
                    "model": VISION_MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url", "image_url": {"url": data_uri}},
                            ],
                        }
                    ],
                    "max_tokens": 900,
                    "temperature": 0.6,
                },
            )
        resp.raise_for_status()
        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        answer = (msg.get("content") or "").strip()
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
