"""scheduler_state — durable layer unit coverage with an in-process fake Redis.

No monkeypatching: the state object takes its client via the constructor and
``has_posted`` takes its client as a keyword argument, so every failure path
(Redis missing, raising, partially written) is exercised as plain fakes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from rugmunchbot.channels import scheduler_state as st
from rugmunchbot.channels.scheduler_state import _config, _singleton, _sync

GLOBAL = "bot:channels:posted:__global__"
LAST_RUN = "bot:channels:last_run"


class FakePipeline:
    def __init__(self, ops: list) -> None:
        self.ops = ops

    def sadd(self, key, *members):
        self.ops.append(("sadd", key, set(members)))

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))

    def hset(self, key, mapping=None):
        self.ops.append(("hset", key, dict(mapping or {})))

    async def execute(self):
        self.ops.append(("execute",))


class FailingPipeline:
    """Pipeline whose EXECUTE fails (call accepted, round-trip lost)."""

    def sadd(self, *a):
        return None

    def expire(self, *a):
        return None

    def hset(self, *a, **k):
        return None

    async def execute(self):
        raise ConnectionError("redis gone")


class FakeRedis:
    """Just the surface the durable layer uses (scan/smembers/hgetall/pipeline)."""

    def __init__(self, sets=None, last_run=None, fail=False, fail_pipeline=False) -> None:
        self.sets = sets or {}
        self.last_run = last_run or {}
        self.fail = fail
        self.fail_pipeline = fail_pipeline
        self.ops: list = []

    async def scan_iter(self, match="", count=100):
        if self.fail:
            raise ConnectionError("redis down")
        for key in self.sets:
            if key.startswith(match.rstrip("*")):
                yield key.encode()

    async def smembers(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        name = key.decode() if isinstance(key, bytes) else key
        return {v.encode() for v in self.sets.get(name, set())}

    async def hgetall(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return {k.encode(): v.encode() for k, v in self.last_run.items()}

    def pipeline(self, transaction=False):
        return FailingPipeline() if self.fail_pipeline else FakePipeline(self.ops)


@pytest.fixture(autouse=True)
def _reset_sync_client():
    """Keep the process-wide sync client/backoff clean between tests."""
    _sync.reset_sync()
    yield
    _sync.reset_sync()


# ── load ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_hydrates_global_set_and_merges_legacy_channels():
    redis = FakeRedis(
        sets={
            GLOBAL: {"https://a", "tgnews:https://b"},
            "bot:channels:posted:rag_news": {"rag:https://c"},
        },
        last_run={"channel_news": "2026-10-01T00:00:00+00:00"},
    )
    seed: dict = {}
    state = st.SchedulerState(redis)
    assert await state.load(seed) is True
    assert set(seed["__global__"]) == {"https://a", "tgnews:https://b", "https://c"}
    assert state.last_run_iso("channel_news") == "2026-10-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_load_strips_lane_prefix_from_legacy_sets():
    redis = FakeRedis(sets={"bot:channels:posted:rag_news": {"rag:https://c"}})
    seed: dict = {}
    await st.SchedulerState(redis).load(seed)
    assert list(seed["__global__"]) == ["https://c"]


@pytest.mark.asyncio
async def test_load_fail_open_disables_layer():
    state = st.SchedulerState(FakeRedis(fail=True))
    assert await state.load({}) is False
    assert state.is_available() is False  # marked down, client cleared


# ── save ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_writes_sets_ttl_and_last_run_hash():
    redis = FakeRedis()
    state = st.SchedulerState(redis)
    state.mark_last_run("channel_news")
    assert await state.save({"rag_news": {"k1", "k2"}, "empty": set()}) is True
    sadds = [op for op in redis.ops if op[0] == "sadd"]
    assert sadds == [("sadd", "bot:channels:posted:rag_news", {"k1", "k2"})]
    assert ("expire", "bot:channels:posted:rag_news", _config.DEFAULT_TTL) in redis.ops
    assert any(op[0] == "hset" and op[1] == LAST_RUN for op in redis.ops)


@pytest.mark.asyncio
async def test_save_failure_is_fail_open():
    state = st.SchedulerState(FakeRedis(fail_pipeline=True))
    assert await state.save({"rag_news": {"k"}}) is False


@pytest.mark.asyncio
async def test_save_without_client_is_false():
    state = st.SchedulerState()
    state._mark_down()
    assert await state.save({"a": {"b"}}) is False


# ── remember ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remember_stores_full_key_and_bare_plus_extra_globally():
    redis = FakeRedis()
    state = st.SchedulerState(redis)
    await state.remember("rag_news", "rag:https://x", ("titlehash",))
    sadd = {op[1]: op[2] for op in redis.ops if op[0] == "sadd"}
    assert sadd["bot:channels:posted:rag_news"] == {"rag:https://x"}
    assert sadd[GLOBAL] == {"https://x", "titlehash"}


@pytest.mark.asyncio
async def test_remember_failure_never_raises():
    redis = FakeRedis()
    state = st.SchedulerState(redis)

    class BoomPipeline:
        def sadd(self, *a):
            return None

        def expire(self, *a):
            return None

        async def execute(self):
            raise ConnectionError("redis gone")

    redis.pipeline = lambda transaction=False: BoomPipeline()
    await state.remember("rag_news", "rag:https://x")  # must not raise


# ── last run ────────────────────────────────────────────────────────────────


def test_last_run_roundtrip():
    state = st.SchedulerState(FakeRedis())
    when = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    state.mark_last_run("job", when)
    assert state.last_run("job") == when
    assert state.last_run_iso("job") == when.isoformat()
    assert state.last_run("never") is None
    assert state.last_run_iso("never") == ""


def test_last_run_corrupt_value_is_none():
    state = st.SchedulerState(FakeRedis())
    state._last_run["job"] = "not-a-date"
    assert state.last_run("job") is None


def test_reset_clears_client_and_timestamps():
    state = st.SchedulerState(FakeRedis())
    state.mark_last_run("job")
    state.reset()
    assert state._redis is None
    assert state.last_run_iso("job") == ""


# ── sync durable check ──────────────────────────────────────────────────────


def test_has_posted_none_client_is_false():
    assert st.has_posted("k", client=None) is False


def test_has_posted_uses_global_set():
    class Client:
        def sismember(self, key, member):
            assert key == GLOBAL
            return member == "known"

    assert st.has_posted("known", client=Client()) is True
    assert st.has_posted("other", client=Client()) is False


def test_has_posted_raising_client_is_fail_open():
    class Client:
        def sismember(self, key, member):
            raise ConnectionError("down")

    assert st.has_posted("k", client=Client()) is False
    _sync.reset_sync()


def test_reset_sync_clears_backoff():
    _sync._sync_down_until = 1e12
    _sync.reset_sync()
    assert _sync._sync_down_until == 0.0


# ── facade / singleton contract ─────────────────────────────────────────────


def test_facade_exports_the_documented_surface():
    for name in st.__all__:
        assert hasattr(st, name), name
    assert st.SchedulerState is not None
    assert st.get_state() is _singleton._state


def test_singleton_helpers_delegate_to_the_state():
    st.reset_state()
    st.mark_last_run("job")
    assert st.last_run_iso("job")
    st.reset_state()
    assert st.last_run_iso("job") == ""


def test_dedup_window_constants_are_consistent():
    assert _config.GLOBAL_POSTED_SET == GLOBAL
    assert _config.POSTED_MAX == 5000
    assert timedelta(days=30).total_seconds() == _config.LAST_RUN_TTL
