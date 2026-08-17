#!/usr/bin/env python3
"""
RugMunchBot — shared admin state + analytics (bot ↔ backend API).

Single source of truth for bot management state that BOTH the bot runtime
(handlers, scheduled jobs) and the backend admin API (app/routers/admin/
bot_admin.py) read/write. All state lives in Redis so the bot (host) and
the backend (Docker) see identical values.

Redis keys:
  bot:admin:state                JSON {"enabled": bool, "chat_enabled": bool}
  bot:admin:banned_users         SET of str(user_id)
  bot:analytics:messages:{date}  INCR counter (YYYY-MM-DD, UTC)
  bot:analytics:ai_queries:{date} INCR counter
  bot:analytics:new_users:{date} INCR counter
  bot:analytics:tokens:{date}    INCRBY counter (estimated LLM tokens)
  bot:analytics:commands         HASH command -> count
  bot:admin:broadcast:queue      LIST of JSON broadcast jobs
  bot:admin:broadcast:{id}       JSON broadcast state

Every function fails open: when Redis is unavailable the bot keeps running
(state defaults to enabled) and analytics are silently dropped.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.redis import get_redis_async
from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger(__name__)

STATE_KEY = "bot:admin:state"
BANNED_KEY = "bot:admin:banned_users"
COMMANDS_KEY = "bot:analytics:commands"
BROADCAST_QUEUE_KEY = "bot:admin:broadcast:queue"
BROADCAST_PREFIX = "bot:admin:broadcast:"

DEFAULT_STATE: dict[str, Any] = {"enabled": True, "chat_enabled": True}


def _day(offset_days: int = 0) -> str:
    """Return YYYY-MM-DD (UTC) for ``offset_days`` ago (0 = today)."""
    return (datetime.now(UTC) - timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _msg_key(d: str | None = None) -> str:
    return f"bot:analytics:messages:{d or _day()}"


def _ai_key(d: str | None = None) -> str:
    return f"bot:analytics:ai_queries:{d or _day()}"


def _new_key(d: str | None = None) -> str:
    return f"bot:analytics:new_users:{d or _day()}"


def _tokens_key(d: str | None = None) -> str:
    return f"bot:analytics:tokens:{d or _day()}"


def _as_str(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


def _as_int(v: Any) -> int:
    if v is None:
        return 0
    if isinstance(v, bytes):
        v = v.decode()
    try:
        return int(v)
    except (ValueError, TypeError):
        return 0


# ══════════════════════════════════════════════
# Bot state (enabled / chat_enabled)
# ══════════════════════════════════════════════


async def get_bot_state() -> dict[str, Any]:
    """Return the full bot state dict, merged over safe defaults.

    Primary source is Redis. When Redis is unavailable, falls back to the
    bot's SQLite ``bot_state`` table so the admin API still reports the
    runtime values the bot is actually using.
    """
    state = dict(DEFAULT_STATE)
    r = get_redis_async()
    if r is None:
        return _sqlite_state_fallback(state)
    try:
        raw = await r.get(STATE_KEY)
        if raw:
            state.update(json.loads(raw))
            return state
    except Exception:
        logger.warning("admin_state:get_bot_state failed", exc_info=True)
        return _sqlite_state_fallback(state)
    # Redis reachable but key unset — mirror SQLite values if any exist.
    return _sqlite_state_fallback(state)


def _sqlite_state_fallback(state: dict[str, Any]) -> dict[str, Any]:
    """Overlay the bot's SQLite bot_state toggles when Redis has no value."""
    try:
        from app.domains.telegram.rugmunchbot import db as bot_db

        enabled = bot_db.get_bot_state("bot_enabled", "on")
        chat_enabled = bot_db.get_bot_state("chat_enabled", "on")
        state["enabled"] = enabled == "on"
        state["chat_enabled"] = chat_enabled == "on"
    except Exception:
        logger.warning("admin_state:sqlite fallback failed", exc_info=True)
    return state


async def set_bot_state(**updates: Any) -> dict[str, Any]:
    """Apply partial updates to bot state and persist to Redis."""
    current = await get_bot_state()
    current.update(updates)
    r = get_redis_async()
    if r is not None:
        try:
            await r.set(STATE_KEY, json.dumps(current))
        except Exception:
            logger.warning("admin_state:set_bot_state failed", exc_info=True)
    return current


async def is_bot_enabled() -> bool:
    return bool((await get_bot_state()).get("enabled", True))


async def is_chat_enabled() -> bool:
    return bool((await get_bot_state()).get("chat_enabled", True))


# ══════════════════════════════════════════════
# Bans
# ══════════════════════════════════════════════


async def is_user_banned(user_id: int) -> bool:
    r = get_redis_async()
    if r is None:
        return False
    try:
        return bool(await r.sismember(BANNED_KEY, str(user_id)))
    except Exception:
        logger.warning("admin_state:is_user_banned failed", exc_info=True)
        return False


async def ban_user(user_id: int) -> bool:
    r = get_redis_async()
    if r is None:
        return False
    try:
        await r.sadd(BANNED_KEY, str(user_id))
        return True
    except Exception:
        logger.warning("admin_state:ban_user failed", exc_info=True)
        return False


async def unban_user(user_id: int) -> bool:
    r = get_redis_async()
    if r is None:
        return False
    try:
        await r.srem(BANNED_KEY, str(user_id))
        return True
    except Exception:
        logger.warning("admin_state:unban_user failed", exc_info=True)
        return False


async def banned_user_ids() -> list[int]:
    r = get_redis_async()
    if r is None:
        return []
    try:
        raw = await r.smembers(BANNED_KEY)
        ids: list[int] = []
        for v in raw:
            s = _as_str(v)
            if s.isdigit():
                ids.append(int(s))
        return sorted(ids)
    except Exception:
        logger.warning("admin_state:banned_user_ids failed", exc_info=True)
        return []


# ══════════════════════════════════════════════
# Analytics counters
# ══════════════════════════════════════════════


async def track_message() -> None:
    await _incr(_msg_key())


async def track_ai_query() -> None:
    await _incr(_ai_key())


async def track_new_user() -> None:
    await _incr(_new_key())


async def track_tokens(n: int) -> None:
    if n <= 0:
        return
    r = get_redis_async()
    if r is None:
        return
    try:
        await r.incrby(_tokens_key(), int(n))
    except Exception:
        logger.warning("admin_state:track_tokens failed", exc_info=True)


async def track_command(command: str) -> None:
    """Record a slash command usage (hash: command -> count)."""
    if not command:
        return
    r = get_redis_async()
    if r is None:
        return
    try:
        await r.hincrby(COMMANDS_KEY, command, 1)
    except Exception:
        logger.warning("admin_state:track_command failed", exc_info=True)


async def _incr(key: str) -> None:
    r = get_redis_async()
    if r is None:
        return
    try:
        await r.incr(key)
    except Exception:
        logger.warning("admin_state:incr failed key=%s", key, exc_info=True)


# ══════════════════════════════════════════════
# Analytics reads (admin API)
# ══════════════════════════════════════════════


async def get_daily_analytics(days: int = 7) -> list[dict[str, Any]]:
    """Return per-day analytics for the last ``days`` days (oldest first)."""
    r = get_redis_async()
    rows: list[dict[str, Any]] = []
    for i in range(max(1, days) - 1, -1, -1):
        d = _day(i)
        row: dict[str, Any] = {"date": d, "messages": 0, "ai_queries": 0, "new_users": 0, "tokens": 0}
        if r is not None:
            try:
                row["messages"] = _as_int(await r.get(_msg_key(d)))
                row["ai_queries"] = _as_int(await r.get(_ai_key(d)))
                row["new_users"] = _as_int(await r.get(_new_key(d)))
                row["tokens"] = _as_int(await r.get(_tokens_key(d)))
            except Exception:
                logger.warning("admin_state:get_daily_analytics failed day=%s", d, exc_info=True)
        rows.append(row)
    return rows


async def get_command_stats(limit: int = 20) -> list[dict[str, Any]]:
    """Return most-used slash commands (descending by count)."""
    r = get_redis_async()
    if r is None:
        return []
    try:
        raw = await r.hgetall(COMMANDS_KEY)
        items = sorted(((_as_str(k), _as_int(v)) for k, v in raw.items()), key=lambda x: x[1], reverse=True)
        return [{"command": k, "count": v} for k, v in items[: max(1, limit)]]
    except Exception:
        logger.warning("admin_state:get_command_stats failed", exc_info=True)
        return []


# ══════════════════════════════════════════════
# Broadcast queue
# ══════════════════════════════════════════════


def new_broadcast(message: str, sender: str | None = None) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "message": message,
        "status": "queued",
        "created_at": datetime.now(UTC).isoformat(),
        "sender": sender or "",
        "total": 0,
        "sent": 0,
        "failed": 0,
    }


async def enqueue_broadcast(message: str, sender: str | None = None) -> dict[str, Any]:
    """Queue a broadcast for delivery. Returns the broadcast record."""
    bcast = new_broadcast(message, sender)
    r = get_redis_async()
    if r is None:
        bcast["status"] = "error"
        bcast["error"] = "redis_unavailable"
        return bcast
    try:
        await r.lpush(BROADCAST_QUEUE_KEY, json.dumps(bcast))
        await r.set(f"{BROADCAST_PREFIX}{bcast['id']}", json.dumps(bcast))
        return bcast
    except Exception as e:
        logger.warning("admin_state:enqueue_broadcast failed", exc_info=True)
        bcast["status"] = "error"
        bcast["error"] = str(e)
        return bcast


async def pop_broadcast() -> dict[str, Any] | None:
    """Pop the oldest queued broadcast (FIFO). Returns None when empty."""
    r = get_redis_async()
    if r is None:
        return None
    try:
        raw = await r.rpop(BROADCAST_QUEUE_KEY)
        if not raw:
            return None
        payload = raw[0] if isinstance(raw, list) else raw
        data = json.loads(payload)
        return data if isinstance(data, dict) else None
    except Exception:
        logger.warning("admin_state:pop_broadcast failed", exc_info=True)
        return None


async def update_broadcast(bcast_id: str, **updates: Any) -> dict[str, Any] | None:
    """Update the persistent state record for a broadcast."""
    r = get_redis_async()
    if r is None:
        return None
    try:
        key = f"{BROADCAST_PREFIX}{bcast_id}"
        raw = await r.get(key)
        cur = json.loads(raw) if raw else {"id": bcast_id}
        cur.update(updates)
        await r.set(key, json.dumps(cur))
        return cur
    except Exception:
        logger.warning("admin_state:update_broadcast failed id=%s", bcast_id, exc_info=True)
        return None


async def list_broadcasts(limit: int = 20) -> list[dict[str, Any]]:
    """Return recent broadcast records (newest first)."""
    r = get_redis_async()
    if r is None:
        return []
    try:
        keys = await r.keys(f"{BROADCAST_PREFIX}*")
        records: list[dict[str, Any]] = []
        for k in keys:
            raw = await r.get(_as_str(k))
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict):
                        records.append(data)
                except (ValueError, TypeError):
                    continue
        records.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return records[: max(1, limit)]
    except Exception:
        logger.warning("admin_state:list_broadcasts failed", exc_info=True)
        return []
