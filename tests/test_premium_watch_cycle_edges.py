"""Watch-cycle edges: every event kind, both listings, the store's veto.

Split from ``test_premium_watch_cycle`` to stay inside the size gate: the core
file proves the cycle contracts (detect once, survive a restart, prune); this
one proves the evidence a card carries and that no stored row means no page.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import fakeredis
import fakeredis.aioredis
import pytest

from rugmunchbot.premium import watch_cycle
from rugmunchbot.premium.bot_queue import QUEUE_KEY
from rugmunchbot.premium.watch_cycle import run_watch_cycle
from rugmunchbot.premium.watch_store import (
    add_watch,
    list_watches,
    load_snapshot,
    watchers_for,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

TOKEN = "So11111111111111111111111111111111111111112"
CHAT_A = "1111"


class Rows:
    """Stand-in for the canonical alert store: records what it was handed."""

    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def __call__(self, session: Any, rows: list[dict[str, Any]]) -> int:
        self.saved.extend(rows)
        return len(rows)


class Deduped(Rows):
    """A store that reports the row already existed (0 inserted)."""

    async def __call__(self, session: Any, rows: list[dict[str, Any]]) -> int:
        return 0


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


async def test_every_event_kind_carries_its_evidence(redis: Any) -> None:
    await add_watch(redis, TOKEN, "solana", CHAT_A)
    await _cycle(redis, _payload(), Rows())

    rows = Rows()
    changed = _payload(
        top_holders=[],
        liquidity={"usd": 40_000.0, "locked_pct": 0.0},
        contract_authority={"owner": "OwnerB"},
        bundle_analysis={"bundles": [{"wallets": ["BundleW"]}]},
    )
    result = await _cycle(redis, changed, rows)

    assert {e["kind"] for e in result["events"]} == {
        "cashed_out",
        "lp_unlocked",
        "new_bundle_wallet",
        "authority_changed",
    }
    bullets = json.loads((await redis.lrange(QUEUE_KEY, 0, -1))[0])["payload"]["changes"]
    assert any("Liquidity: $100,000 -> $40,000" in line for line in bullets)
    assert any("owner:" in line for line in bullets), "the card must name what moved"


async def test_a_store_that_inserts_nothing_pages_nobody(redis: Any) -> None:
    """The store dedupes; a replayed cycle must not page the same chat twice."""
    await add_watch(redis, TOKEN, "solana", CHAT_A)
    await _cycle(redis, _payload(), Rows())

    result = await _cycle(redis, _payload(top_holders=[]), Deduped())

    assert result["events"], "the delta was real"
    assert result["inserted"] == 0
    assert result["queued"] == 0
    assert await redis.llen(QUEUE_KEY) == 0, "no stored row means no page"


async def test_listings_read_both_indexes_and_survive_a_corrupt_snapshot(redis: Any) -> None:
    await add_watch(redis, TOKEN, "solana", CHAT_A)

    by_chat = await list_watches(redis, CHAT_A)
    by_scan = await list_watches(redis)
    assert by_chat[0]["watching"] is True
    assert by_chat[0]["watchers_n"] == 1
    assert by_scan[0]["token"] == TOKEN
    assert by_scan[0]["chain"] == "solana"

    await redis.set(f"premium:snapshot:solana:{TOKEN}", "{not-json")
    assert await load_snapshot(redis, TOKEN, "solana") is None, "junk is a missing baseline"


async def test_a_text_client_decodes_the_same_watchers(server: Any) -> None:
    text = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    await add_watch(text, TOKEN, "solana", CHAT_A)
    assert await watchers_for(text, TOKEN, "solana") == [CHAT_A]
    await text.aclose()
