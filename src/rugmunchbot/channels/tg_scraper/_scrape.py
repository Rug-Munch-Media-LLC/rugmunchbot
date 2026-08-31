"""Dialog listing + message scraping via the headless Telethon client.

Lists EVERY dialog the account is a member of (``iter_dialogs``), then reads
recent messages per dialog with:
  * a lookback window (default 24h),
  * per-dialog message limits,
  * random jitter between dialogs (anti-ban pacing),
  * FloodWait fail-open (honor short waits, skip the dialog on long ones),
  * bounded optional photo download for evidence screenshots.

Fail-open everywhere: a dead client, one broken dialog, or a flood limit
never aborts the sweep — it degrades to fewer messages and logs.
"""

from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from rugmunchbot.channels.tg_scraper._config import (
    TG_SCRAPER_DOWNLOAD_MEDIA,
    TG_SCRAPER_FLOOD_WAIT_CAP,
    TG_SCRAPER_JITTER_MAX,
    TG_SCRAPER_JITTER_MIN,
    TG_SCRAPER_MAX_CHANNELS,
    TG_SCRAPER_MEDIA_BUDGET,
    TG_SCRAPER_MEDIA_DIR,
    TG_SCRAPER_PER_CHANNEL_LIMIT,
    logger,
)

#: Dialog types worth scraping. Telethon dialog objects expose ``is_user`` /
#: ``is_group`` / ``is_channel``; private 1:1 chats are skipped (no shill
#: value, huge privacy surface).
_SKIP_PRIVATE = True


@dataclass(slots=True)
class TgMessage:
    """One scraped Telegram message (superset of ChannelMessage)."""

    channel: str
    channel_username: str
    message_id: int
    text: str = ""
    media_type: str = "text"
    media_path: str | None = None
    posted_at: datetime | None = None
    forwarded_from: str | None = None
    reply_to: int | None = None
    sender_id: int | None = None
    sender_handle: str | None = None
    sender_name: str | None = None
    views: int | None = None


async def _build_headless_client() -> Any:
    """Return the singleton headless Telethon client, or ``None`` (never raises)."""
    try:
        from rugmunchbot._vendor.headless import get_headless_client

        client = await get_headless_client()
        if getattr(client, "is_connected", False):
            return client
        return None
    except Exception as exc:  # fail-open by contract
        logger.warning("tg_scraper:headless_client_unavailable err=%s", str(exc)[:200])
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _dialog_username(dialog: Any) -> str:
    """Best-effort @username (or numeric id fallback) for a dialog. Never raises."""
    try:
        entity = getattr(dialog, "entity", None)
        username = getattr(entity, "username", None)
        if username:
            return str(username).lstrip("@")
        chat = getattr(dialog, "name", None) or ""
        if chat:
            return ""
        return ""
    except Exception:
        return ""


def _dialog_key(dialog: Any) -> str:
    """Stable per-dialog key used for dedup + logging. Never raises."""
    try:
        entity = getattr(dialog, "entity", None)
        did = getattr(entity, "id", None) or getattr(dialog, "id", None)
        return str(did) if did is not None else _dialog_username(dialog) or "unknown"
    except Exception:
        return "unknown"


def _is_scrapable(dialog: Any) -> bool:
    """True for channels/groups (skip private chats + our own channels)."""
    try:
        if _SKIP_PRIVATE and getattr(dialog, "is_user", False):
            return False
        title = str(getattr(dialog, "name", "") or "").lower()
        # Skip our own channels (no shill value, avoid feedback loops).
        return "rugmunch" not in title
    except Exception:
        return False


async def list_dialogs(client: Any, max_dialogs: int | None = None) -> list[Any]:
    """List all scrapable dialogs the account is a member of. Never raises.

    Accepts the HeadlessTelegramClient wrapper (its underlying Telethon
    ``_client.iter_dialogs`` is used) or any duck-typed client exposing
    ``iter_dialogs()``.
    """
    limit = max(1, max_dialogs or TG_SCRAPER_MAX_CHANNELS)
    try:
        if client is None:
            return []
        raw = getattr(client, "_client", None) or client
        iter_dialogs = getattr(raw, "iter_dialogs", None)
        if not callable(iter_dialogs):
            logger.debug("tg_scraper:no_iter_dialogs")
            return []
        dialogs: list[Any] = []
        async for dialog in iter_dialogs():
            dialogs.append(dialog)
            if len(dialogs) >= limit:
                break
        scrapable = [d for d in dialogs if _is_scrapable(d)]
        logger.info("tg_scraper:dialogs total=%d scrapable=%d", len(dialogs), len(scrapable))
        return scrapable
    except Exception as exc:
        logger.warning("tg_scraper:list_dialogs_failed err=%s", str(exc)[:200])
        return []


def _sender_fields(msg: Any) -> tuple[int | None, str | None, str | None]:
    """Extract (sender_id, handle, display name) from a Telethon message."""
    try:
        sender = getattr(msg, "sender", None)
        sender_id = getattr(msg, "sender_id", None)
        if sender is None:
            return (int(sender_id) if sender_id is not None else None, None, None)
        handle = getattr(sender, "username", None)
        name = getattr(sender, "first_name", None) or ""
        last = getattr(sender, "last_name", None) or ""
        full = f"{name} {last}".strip() or None
        return (
            int(sender_id) if sender_id is not None else None,
            str(handle).lstrip("@") if handle else None,
            full,
        )
    except Exception:
        return (None, None, None)


def _to_tg_message(msg: Any, channel_username: str, channel_title: str) -> TgMessage | None:
    """Convert a duck-typed Telethon message to a TgMessage (None on failure)."""
    try:
        message_id = getattr(msg, "id", None)
        if message_id is None:
            return None
        text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
        text = text if isinstance(text, str) else str(text)
        sender_id, handle, name = _sender_fields(msg)
        forwarded_from: str | None = None
        fwd = getattr(msg, "fwd_from", None)
        if fwd is not None:
            forwarded_from = getattr(fwd, "from_name", None)
            if forwarded_from is None:
                peer = getattr(fwd, "from_id", None)
                forwarded_from = str(peer) if peer is not None else "forwarded"
        reply_to = getattr(msg, "reply_to_msg_id", None)
        if reply_to is None:
            reply_header = getattr(msg, "reply_to", None)
            reply_to = getattr(reply_header, "reply_to_msg_id", None) if reply_header else None
        media = getattr(msg, "media", None)
        media_type = "text" if media is None else type(media).__name__.lower()
        for kind in ("photo", "video", "document", "audio", "sticker", "voice"):
            if kind in media_type:
                media_type = kind
                break
        views = getattr(msg, "views", None)
        return TgMessage(
            channel=channel_title or channel_username,
            channel_username=channel_username,
            message_id=int(message_id),
            text=text,
            media_type=media_type,
            posted_at=getattr(msg, "date", None),
            forwarded_from=forwarded_from,
            reply_to=int(reply_to) if reply_to is not None else None,
            sender_id=sender_id,
            sender_handle=handle,
            sender_name=name,
            views=int(views) if views is not None else None,
        )
    except Exception:
        return None


def _flood_wait_seconds(exc: Exception) -> float | None:
    """Extract the wait seconds from a Telethon FloodWaitError, else None."""
    seconds = getattr(exc, "seconds", None)
    if seconds is None:
        return None
    try:
        return float(seconds)
    except (TypeError, ValueError):
        return None


async def _download_media(msg: Any, budget: int) -> str | None:
    """Download one photo to the media dir (bounded, best-effort). Never raises."""
    if budget <= 0 or not TG_SCRAPER_DOWNLOAD_MEDIA:
        return None
    try:
        os.makedirs(TG_SCRAPER_MEDIA_DIR, exist_ok=True)
        raw = getattr(msg, "download_media", None)
        if not callable(raw):
            return None
        path = await asyncio.wait_for(raw(file=TG_SCRAPER_MEDIA_DIR), timeout=20.0)
        return str(path) if path else None
    except Exception as exc:
        logger.debug("tg_scraper:media_download_failed err=%s", str(exc)[:120])
        return None


async def scrape_dialog(
    client: Any,
    dialog: Any,
    *,
    lookback_hours: int | None = None,
    per_channel_limit: int | None = None,
    media_budget: int = 0,
) -> list[TgMessage]:
    """Read recent messages from ONE dialog inside the lookback window.

    FloodWait fail-open: short waits are honored (capped), long waits skip
    the dialog. Any other error degrades to whatever was already collected.
    """
    limit = max(1, per_channel_limit or TG_SCRAPER_PER_CHANNEL_LIMIT)
    hours = max(1, lookback_hours or 24)
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    key = _dialog_key(dialog)
    username = _dialog_username(dialog)
    title = str(getattr(dialog, "name", "") or "") or username
    try:
        raw = getattr(client, "_client", None) or client
        iter_messages = getattr(raw, "iter_messages", None)
        if not callable(iter_messages):
            return []
        messages: list[TgMessage] = []
        media_left = media_budget
        async for msg in iter_messages(dialog, limit=limit):
            converted = _to_tg_message(msg, username, title)
            if converted is None:
                continue
            posted = converted.posted_at
            if posted is not None and _as_utc(posted) < cutoff:
                continue
            if converted.media_type == "photo" and media_left > 0:
                converted.media_path = await _download_media(msg, media_left)
                if converted.media_path:
                    media_left -= 1
            messages.append(converted)
        return messages
    except Exception as exc:
        wait = _flood_wait_seconds(exc)
        if wait is not None:
            if wait <= TG_SCRAPER_FLOOD_WAIT_CAP:
                logger.warning("tg_scraper:flood_wait dialog=%s wait=%.0fs", key, wait)
                await asyncio.sleep(wait)
            else:
                logger.warning(
                    "tg_scraper:flood_wait_skip dialog=%s wait=%.0fs cap=%.0fs",
                    key,
                    wait,
                    TG_SCRAPER_FLOOD_WAIT_CAP,
                )
            return []
        logger.warning("tg_scraper:dialog_read_failed dialog=%s err=%s", key, str(exc)[:160])
        return []


async def scrape_all_dialogs(
    client: Any,
    *,
    lookback_hours: int | None = None,
    per_channel_limit: int | None = None,
    max_dialogs: int | None = None,
    media_budget: int | None = None,
) -> list[TgMessage]:
    """Scrape every scrapable dialog with jittered pacing between fetches.

    Returns messages newest-window filtered, deduped on (channel, message_id).
    Never raises.
    """
    dialogs = await list_dialogs(client, max_dialogs)
    if not dialogs:
        return []
    budget = TG_SCRAPER_MEDIA_BUDGET if media_budget is None else media_budget
    all_messages: list[TgMessage] = []
    seen: set[tuple[str, int]] = set()
    for dialog in dialogs:
        batch = await scrape_dialog(
            client,
            dialog,
            lookback_hours=lookback_hours,
            per_channel_limit=per_channel_limit,
            media_budget=budget,
        )
        budget = max(0, budget - sum(1 for m in batch if m.media_path))
        for message in batch:
            key = (message.channel_username or message.channel, message.message_id)
            if key in seen:
                continue
            seen.add(key)
            all_messages.append(message)
        if TG_SCRAPER_JITTER_MAX > 0:
            delay = random.uniform(TG_SCRAPER_JITTER_MIN, TG_SCRAPER_JITTER_MAX)  # noqa: S311 — pacing jitter, not crypto
            await asyncio.sleep(delay)
    logger.info(
        "tg_scraper:sweep_done dialogs=%d messages=%d media=%d",
        len(dialogs),
        len(all_messages),
        sum(1 for m in all_messages if m.media_path),
    )
    return all_messages
