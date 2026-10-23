"""Persistent per-chat store for community features (Redis, memory fallback).

Only hash/set primitives are used, so a test double is a dozen lines and the
production path reuses the bot's existing async Redis connection. A Redis
outage degrades to process memory (entries are lost on restart) and never
raises into the dispatcher: community features are convenience, never safety.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

from rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.moderation.store")

NS = os.getenv("RUGMUNCH_MODERATION_NS", "rmi:mod:v1")
MAX_FIELDS = 200


class HashStore(Protocol):
    """The narrow slice of ``redis.asyncio`` this package relies on."""

    async def hget(self, key: str, field: str) -> Any: ...

    async def hset(self, key: str, field: str, value: str) -> Any: ...

    async def hgetall(self, key: str) -> dict[str, Any]: ...

    async def hdel(self, key: str, field: str) -> Any: ...

    async def sadd(self, key: str, member: str) -> Any: ...

    async def srem(self, key: str, member: str) -> Any: ...

    async def sismember(self, key: str, member: str) -> bool: ...

    async def smembers(self, key: str) -> Any: ...


class MemoryStore:
    """Process-local double with the redis.asyncio method names."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.sets: dict[str, set[str]] = {}

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    async def hdel(self, key: str, field: str) -> int:
        return int(self.hashes.get(key, {}).pop(field, None) is not None)

    async def sadd(self, key: str, member: str) -> int:
        bucket = self.sets.setdefault(key, set())
        fresh = member not in bucket
        bucket.add(member)
        return int(fresh)

    async def srem(self, key: str, member: str) -> int:
        bucket = self.sets.get(key, set())
        present = member in bucket
        bucket.discard(member)
        return int(present)

    async def sismember(self, key: str, member: str) -> bool:
        return member in self.sets.get(key, set())

    async def smembers(self, key: str) -> set[str]:
        return set(self.sets.get(key, set()))


_memory = MemoryStore()
_override: HashStore | None = None
_redis_failed = False


def use_backend(client: HashStore | None) -> None:
    """Inject a client (tests) or force the memory fallback with ``None``."""
    global _override, _redis_failed
    _override = client
    _redis_failed = False


def redis_enabled() -> bool:
    """Redis persistence is opt-in: an operator sets RUGMUNCH_MODERATION_REDIS=1.

    Default off keeps the import path side-effect free (the vendor client may
    resolve a password through gopass) and makes tests deterministic.
    """
    return os.getenv("RUGMUNCH_MODERATION_REDIS", "").strip().lower() in {"1", "true", "yes", "on"}


def client() -> HashStore:
    """The active client: injected, else opt-in Redis, else process memory."""
    global _redis_failed
    if _override is not None:
        return _override
    if not _redis_failed and redis_enabled():
        try:
            from rugmunchbot._vendor.redis_client import get_redis_async

            redis = get_redis_async()
            if redis is not None:
                return redis
        except Exception:  # any import/connect failure degrades to memory
            logger.info("community store: redis unavailable, memory fallback", exc_info=True)
        _redis_failed = True
    return _memory


def chat_key(chat_id: int, kind: str) -> str:
    """Namespaced key for one chat's *kind* bucket (notes, filters, ...)."""
    return f"{NS}:{int(chat_id)}:{kind}"


def global_key(kind: str) -> str:
    """Namespaced key shared by every chat (global ban list)."""
    return f"{NS}:global:{kind}"


async def get_field(key: str, field: str) -> str | None:
    value = await client().hget(key, field)
    return None if value is None else str(value)


async def set_field(key: str, field: str, value: str, *, max_fields: int = MAX_FIELDS) -> bool:
    """Write one field. Returns False when the bucket is full (no eviction)."""
    current = await client().hgetall(key)
    if field not in current and len(current) >= max_fields:
        return False
    await client().hset(key, field, value)
    return True


async def del_field(key: str, field: str) -> bool:
    return bool(await client().hdel(key, field))


async def all_fields(key: str) -> dict[str, str]:
    raw = await client().hgetall(key)
    return {str(k): str(v) for k, v in (raw or {}).items()}


async def add_member(key: str, member: str) -> bool:
    return bool(await client().sadd(key, member))


async def del_member(key: str, member: str) -> bool:
    return bool(await client().srem(key, member))


async def has_member(key: str, member: str) -> bool:
    return bool(await client().sismember(key, member))


async def members(key: str) -> set[str]:
    return {str(m) for m in (await client().smembers(key) or set())}


def reset_memory() -> None:
    """Drop the process-local fallback (tests, and after a config reload)."""
    _memory.hashes.clear()
    _memory.sets.clear()


__all__ = [
    "NS",
    "HashStore",
    "MemoryStore",
    "add_member",
    "all_fields",
    "chat_key",
    "client",
    "del_field",
    "del_member",
    "get_field",
    "global_key",
    "has_member",
    "members",
    "redis_enabled",
    "reset_memory",
    "set_field",
    "use_backend",
]
