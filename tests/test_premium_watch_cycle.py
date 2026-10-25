"""End-to-end premium watch cycle against a fake Redis server.

The pipeline's promises are only provable across cycles: a change is detected
once, a restart does not re-page it, and a token nobody watches leaves nothing
behind. Units cover the pieces; this file covers the seam between them, with
the alert store installed through ``watch_cycle.set_writer`` (no patching).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import fakeredis
import fakeredis.aioredis
import pytest
from rugmunchbot.premium.watch_cycle import run_watch_cycle

from rugmunchbot.premium import watch_cycle
from rugmunchbot.premium.bot_queue import QUEUE_KEY
from rugmunchbot.premium.watch_store import (
    add_watch,
    list_watches,
    load_snapshot,
    remove_watch,
    watcher_count,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

TOKEN = "So11111111111111111111111111111111111111112"
CHAT_A = "1111"
CHAT_B = "2222"


class Rows:
    """Stand-in for the canonical alert store: records what it was handed."""

    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def __call__(self, session: Any, rows: list[dict[str, Any]]) -> int:
        self.saved.extend(rows)
        return len(rows)


def _payload(**over: Any) -> dict[str, Any]:
    """A scanner payload: locked LP, one 9.5% holder, a known owner."""
    base: dict[str, Any] = {
        "token": TOKEN,
        "chain": "solana",
        "token_symbol": "RUG",
        "scanned_at": "2026-10-25T00:00:00+00:00",
        "liquidity": {"usd": 100_000.0, "locked_pct": 100.0},
        "top_holders": [{"address": "WhaleA", "pct": 9.5}],
        "contract_authority": {"owner": "OwnerA"},
    }
    base.update(over)
    return base


async def _cycle(redis: Any, payload: dict[str, Any], rows: Rows) -> dict[str, Any]:
    """Run one cycle with ``rows`` installed as the alert store."""
    watch_cycle.set_writer(rows)
    return await run_watch_cycle(None, redis, TOKEN, "solana", payload)


@pytest.fixture(autouse=True)
def _restore_writer() -> Iterator[None]:
    yield
    watch_cycle.set_writer()


@pytest.fixture
def server() -> Any:
    return fakeredis.FakeServer()


@pytest.fixture
async def redis(server: Any) -> Any:
    client = fakeredis.aioredis.FakeRedis(server=server)
    yield client
    await client.aclose()


async def test_first_cycle_is_a_baseline_not_an_event(redis: Any) -> None:
    await add_watch(redis, TOKEN, "solana", CHAT_A)
    rows = Rows()

    result = await _cycle(redis, _payload(), rows)

    assert result["baseline"] is False
    assert result["events"] == []
    assert rows.saved == []
    assert result["queued"] == 0
    assert await redis.llen(QUEUE_KEY) == 0
    assert (await load_snapshot(redis, TOKEN, "solana"))["token"] == TOKEN
    # The baseline is stored with a TTL, so an abandoned token prunes itself.
    assert await redis.ttl(f"premium:snapshot:solana:{TOKEN}") > 0


async def test_change_pages_every_watcher_once_and_never_twice(redis: Any) -> None:
    await add_watch(redis, TOKEN, "solana", CHAT_A)
    await add_watch(redis, TOKEN, "solana", CHAT_B)
    rows = Rows()
    await _cycle(redis, _payload(), rows)

    gone = _payload(top_holders=[])
    first = await _cycle(redis, gone, rows)

    assert [e["kind"] for e in first["events"]] == ["cashed_out"]
    assert first["queued"] == 2
    items = [json.loads(raw) for raw in await redis.lrange(QUEUE_KEY, 0, -1)]
    assert {item["user_id"] for item in items} == {int(CHAT_A), int(CHAT_B)}
    assert {item["type"] for item in items} == {"token.monitor_change"}

    # Same payload again: the baseline already equals it, so the dedupe window
    # holds and the queue does not grow.
    again = await _cycle(redis, gone, rows)
    assert again["events"] == []
    assert again["queued"] == 0
    assert await redis.llen(QUEUE_KEY) == 2


async def test_restart_reloads_the_baseline_from_redis(server: Any) -> None:
    before = fakeredis.aioredis.FakeRedis(server=server)
    await add_watch(before, TOKEN, "solana", CHAT_A)
    await _cycle(before, _payload(), Rows())
    await before.aclose()

    # A new process: same server, fresh client, no in-memory state.
    after = fakeredis.aioredis.FakeRedis(server=server)
    rows = Rows()
    result = await _cycle(after, _payload(top_holders=[]), rows)

    assert result["baseline"] is True
    assert [e["kind"] for e in result["events"]] == ["cashed_out"]
    assert result["queued"] == 1
    assert len(rows.saved) == 1
    await after.aclose()


async def test_removed_watchers_prune_both_the_listing_and_the_rows(redis: Any) -> None:
    await add_watch(redis, TOKEN, "solana", CHAT_A)
    await add_watch(redis, TOKEN, "solana", CHAT_B)
    await _cycle(redis, _payload(), Rows())

    assert await remove_watch(redis, TOKEN, "solana", CHAT_A) is True
    assert await watcher_count(redis, TOKEN, "solana") == 1
    rows = Rows()
    change = await _cycle(redis, _payload(top_holders=[]), rows)
    assert change["queued"] == 1, "only the remaining watcher is paged"

    assert await remove_watch(redis, TOKEN, "solana", CHAT_B) is True
    assert await watcher_count(redis, TOKEN, "solana") == 0
    assert await list_watches(redis) == [], "an empty watch hash is not a watch"

    # An unwatched token keeps a warm baseline but must never fill the table.
    unwatched = Rows()
    quiet = await _cycle(redis, _payload(), unwatched)
    assert unwatched.saved == []
    assert quiet["queued"] == 0
    assert quiet["baseline"] is True, "the baseline still refreshes while unwatched"
