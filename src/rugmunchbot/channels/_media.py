"""Photo-aware channel posting (best-effort, fail-open).

Used by the tg_news broadcast path (``channels/telegram_news/_scheduler.py``)
to attach an article's image (og:image / inline image) to digest and breaking
posts. Any failure — a non-http(s) URL, a Telegram error, an oversized image —
falls back to a plain HTML text message with web-page previews ENABLED
(``disable_web_page_preview=False``) so the caption's link still renders a
preview. Mirrors the channel posters' contract: a poster never crashes the
JobQueue.
"""

from __future__ import annotations

from typing import Any

from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.channels.media")


def is_http_image_url(value: object) -> bool:
    """True when *value* is a usable http(s) image URL (not None/empty/data:)."""
    if not isinstance(value, str):
        return False
    url = value.strip()
    return url.startswith(("http://", "https://"))


async def send_news_photo(bot: Any, chat_id: str, caption: str, photo_url: str) -> bool:
    """Send *photo_url* as a photo with an HTML *caption*; text-fallback on failure.

    Best-effort + fail-open:

    * a non-http(s) ``photo_url`` (None / empty / data:) is rejected up front;
    * any send failure (bad URL, oversized image, caption too long, network
      error) falls back to ``bot.send_message`` with the same HTML caption
      and ``disable_web_page_preview=False`` (the caption's link previews);
    * returns True when either path sent the message; never raises.
    """
    if not is_http_image_url(photo_url):
        logger.debug("channels:send_photo_skip_bad_url url=%s", str(photo_url)[:120])
        return await _send_text(bot, chat_id, caption)
    try:
        await bot.send_photo(
            chat_id=chat_id,
            photo=photo_url,
            caption=caption,
            parse_mode=ParseMode.HTML,
        )
        return True
    except (TelegramError, ValueError, TypeError, KeyError, OSError) as exc:
        logger.warning("channels:send_photo_failed chat=%s err=%s", chat_id, exc)
    except Exception as exc:  # a poster must never crash the JobQueue
        logger.warning("channels:send_photo_unexpected chat=%s err=%s", chat_id, exc)
    return await _send_text(bot, chat_id, caption)


async def _send_text(bot: Any, chat_id: str, caption: str) -> bool:
    """Fallback text send (web-page previews ON) — True when the message sent."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        return True
    except Exception as exc:  # never raise out of a poster
        logger.warning("channels:send_text_fallback_failed chat=%s err=%s", chat_id, exc)
        return False


__all__ = ["is_http_image_url", "send_news_photo"]
