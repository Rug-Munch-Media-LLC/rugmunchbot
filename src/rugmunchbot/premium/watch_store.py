"""Storage for per-token watches and their last snapshot.

Two Redis structures, both expiring, both keyed by chain+token:

``premium:watch:<chain>:<token>``
    hash of ``chat_id -> {"since": iso}``. The hash *is* the watcher set, so
    ``watchers_n`` is its size — no counter to drift out of sync.
``premium:snapshot:<chain>:<token>``
    the last snapshot, so the next cycle has something to diff against.

A chat index (``premium:watch:chat:<chat_id>``) keeps ``GET /watch`` cheap
without scanning every token key. Redis is the right store here: a watch list
is small, write-heavy, read on every cycle, and losing it costs a re-subscribe
— not money.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

WATCH_TTL_SECONDS = 90 * 24 * 3600
SNAPSHOT_TTL_SECONDS = 30 * 24 * 3600


def _text(value: Any) -> str:
    """Redis returns bytes on the shared pool (``decode_responses=False``)."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode()
    return str(value)


def _watch_key(token: str, chain: str) -> str:
    return f"premium:watch:{chain}:{token}"


def _chat_key(chat_id: str) -> str:
    return f"premium:watch:chat:{chat_id}"


def _snapshot_key(token: str, chain: str) -> str:
    return f"premium:snapshot:{chain}:{token}"


async def add_watch(redis: Any, token: str, chain: str, chat_id: str) -> int:
    """Subscribe ``chat_id`` to ``token``; return the resulting watcher count."""
    key = _watch_key(token, chain)
    await redis.hset(key, chat_id, json.dumps({"since": datetime.now(UTC).isoformat()}))
    await redis.expire(key, WATCH_TTL_SECONDS)
    member = f"{chain}:{token}"
    await redis.sadd(_chat_key(chat_id), member)
    await redis.expire(_chat_key(chat_id), WATCH_TTL_SECONDS)
    return int(await redis.hlen(key))


async def remove_watch(redis: Any, token: str, chain: str, chat_id: str) -> bool:
    """Unsubscribe ``chat_id``; ``True`` when a subscription actually existed."""
    removed = int(await redis.hdel(_watch_key(token, chain), chat_id))
    await redis.srem(_chat_key(chat_id), f"{chain}:{token}")
    return removed > 0


async def watchers_for(redis: Any, token: str, chain: str) -> list[str]:
    """Every chat watching this token, as a stable sorted list."""
    return sorted(_text(chat) for chat in await redis.hkeys(_watch_key(token, chain)))


async def watcher_count(redis: Any, token: str, chain: str) -> int:
    """``watchers_n`` — read from the hash size, never from a counter."""
    return int(await redis.hlen(_watch_key(token, chain)))


async def list_watches(redis: Any, chat_id: str = "") -> list[dict[str, Any]]:
    """Watches for one chat, or every token that has at least one watcher."""
    tokens: set[str] = set()
    if chat_id:
        tokens = {_text(member) for member in await redis.smembers(_chat_key(chat_id))}
    else:
        async for key in redis.scan_iter(match="premium:watch:*:*"):
            parts = _text(key).split(":")
            if len(parts) == 4 and parts[2] != "chat":
                tokens.add(f"{parts[2]}:{parts[3]}")
    rows: list[dict[str, Any]] = []
    for member in sorted(tokens):
        chain, _, token = member.partition(":")
        chats = await watchers_for(redis, token, chain)
        if not chats:
            continue
        rows.append(
            {
                "token": token,
                "chain": chain,
                "watchers_n": len(chats),
                "watchers": chats,
                "watching": chat_id in chats if chat_id else True,
            }
        )
    return rows


async def load_snapshot(redis: Any, token: str, chain: str) -> dict[str, Any] | None:
    """The snapshot stored on the previous cycle, if any."""
    raw = await redis.get(_snapshot_key(token, chain))
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


async def save_snapshot(redis: Any, token: str, chain: str, snapshot: dict[str, Any]) -> None:
    """Store this cycle's snapshot as the baseline for the next one."""
    await redis.set(_snapshot_key(token, chain), json.dumps(snapshot), ex=SNAPSHOT_TTL_SECONDS)
