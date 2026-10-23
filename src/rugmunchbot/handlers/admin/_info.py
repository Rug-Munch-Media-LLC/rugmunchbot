"""Informational commands: /alerts, /budget and /sources."""

from __future__ import annotations

import html
import json
import sqlite3
from typing import TYPE_CHECKING

from rugmunchbot.handlers.admin import _reply
from rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.admin")

#: Severity -> emoji used by /alerts.
_SEVERITY_EMOJI = {
    "critical": "\U0001f534",
    "high": "\U0001f7e0",
    "medium": "\U0001f7e1",
    "low": "\U0001f7e2",
}
_INFO_EMOJI = "\u26aa"

#: Content-source kinds counted by /sources.
_SOURCE_KINDS: tuple[str, ...] = ("rss", "telegram", "x")


async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show recent system alerts from the Redis queue (owner only)."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    import redis.asyncio as aioredis
    from redis.exceptions import RedisError

    from rugmunchbot._vendor.alert_pipeline import (
        REDIS_HOST,
        REDIS_PORT,
        REDIS_PW,
        SYSTEM_ALERT_QUEUE_KEY,
    )

    client = aioredis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PW or None,
        decode_responses=True,
        protocol=2,
    )
    try:
        raw = await client.lrange(SYSTEM_ALERT_QUEUE_KEY, 0, 9)
    except (RedisError, OSError) as exc:
        logger.warning("admin_alerts_redis_failed err=%s", str(exc)[:120])
        await _reply._reply(update, "\u274c Could not read alerts from Redis.")
        return
    finally:
        await client.close()

    if not raw:
        await _reply._reply(update, "\U0001f4e6 <b>No system alerts</b> in the queue.")
        return

    lines: list[str] = ["\U0001f4e6 <b>Recent System Alerts</b>", "\u2500" * 20]
    for entry in raw:
        try:
            item = json.loads(entry)
        except (json.JSONDecodeError, TypeError):
            continue
        emoji = _SEVERITY_EMOJI.get(item.get("severity", "info"), _INFO_EMOJI)
        title = html.escape(str(item.get("title") or "alert"))
        desc = html.escape(str(item.get("description") or ""))[:120]
        lines.append(f"{emoji} <b>{title}</b>\n  {desc}")
        if len(lines) >= 12:
            break

    await _reply._reply(update, "\n".join(lines))


async def cmd_budget(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show X-intel budget status (owner only)."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    await _reply._reply(
        update,
        "\U0001f4b0 <b>X-intel budget</b>\n"
        "\u2500" * 20 + "\nX ingestion is owned by <b>rugbrain</b> (RAG). The bot no longer "
        "meters an ApiPass/GetXAPI budget.",
    )


def _count_sources(conn: sqlite3.Connection) -> dict[str, int]:
    """Count active content sources per kind, logging and zeroing failed queries."""
    counts: dict[str, int] = {}
    for kind in _SOURCE_KINDS:
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM content_sources WHERE kind = ? AND active = 1",
                (kind,),
            ).fetchone()
        except sqlite3.Error as exc:
            logger.warning("admin_sources_count_failed kind=%s err=%s", kind, str(exc)[:120])
            counts[kind] = 0
            continue
        counts[kind] = int(row["c"]) if row is not None else 0
    return counts


async def cmd_sources(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show count of active content sources (RSS + Telegram + X) (owner only)."""
    if _reply._deny(update):
        await _reply._reply(update, "\u26a0\ufe0f <b>Owner only.</b>")
        return

    from rugmunchbot import db

    counts: dict[str, int] = dict.fromkeys(_SOURCE_KINDS, 0)
    try:
        conn = db.get_db()
        try:
            counts = _count_sources(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("admin_sources_db_failed err=%s", str(exc)[:120])

    rss, tg, x = counts["rss"], counts["telegram"], counts["x"]
    lines = [
        "\U0001f4e1 <b>Active Content Sources</b>",
        "\u2500" * 20,
        f"\U0001f4f0 RSS: <b>{rss}</b>",
        f"\U0001f4ac Telegram: <b>{tg}</b>",
        f"\U0001f4e2 X/Twitter: <b>{x}</b>",
        f"\U0001f4ca Total: <b>{rss + tg + x}</b>",
    ]
    await _reply._reply(update, "\n".join(lines))
