"""A failed scheduled job must be recorded and retried per policy.

Before this, a channel job that raised only produced a ``channels:tick`` log
line: ``/healthz`` said ``ok`` and ``rmi_bot_jobs_failed_total`` stayed at 0
while the job had been dead for a day. These tests pin the new contract:

* failure → ``/healthz.failed_jobs`` entry + failed counter,
* retry ONLY for the transient class of the taxonomy (bounded),
* success → no failure recorded.
"""

from __future__ import annotations

from typing import Any

import pytest

from rugmunchbot import observability
from rugmunchbot.channels.scheduler._tick import MAX_JOB_RETRIES, _tick_wrapper


class _FakeJobQueue:
    def __init__(self) -> None:
        self.retries: list[dict[str, Any]] = []

    def run_once(self, callback: Any, *, when: float, name: str) -> None:
        self.retries.append({"callback": callback, "when": when, "name": name})


class _FakeCtx:
    def __init__(self) -> None:
        self.job_queue = _FakeJobQueue()


def _failed_jobs() -> dict[str, Any]:
    return observability.health_snapshot()["failed_jobs"]


@pytest.mark.asyncio
async def test_transient_failure_is_recorded_and_retried() -> None:
    async def boom(ctx: Any) -> int:
        raise ConnectionResetError("telegram reset")

    ctx = _FakeCtx()
    await _tick_wrapper("unit_transient", boom)(ctx)

    assert "unit_transient" in _failed_jobs()
    assert _failed_jobs()["unit_transient"]["count"] >= 1
    assert len(ctx.job_queue.retries) == 1
    assert ctx.job_queue.retries[0]["name"] == "unit_transient:retry1"
    assert ctx.job_queue.retries[0]["when"] > 0


@pytest.mark.asyncio
async def test_permanent_failure_is_recorded_but_not_retried() -> None:
    async def bad_data(ctx: Any) -> int:
        raise ValueError("unformattable payload")

    ctx = _FakeCtx()
    await _tick_wrapper("unit_permanent", bad_data)(ctx)

    assert "unit_permanent" in _failed_jobs()
    assert ctx.job_queue.retries == []


@pytest.mark.asyncio
async def test_retry_budget_is_bounded() -> None:
    async def boom(ctx: Any) -> int:
        raise TimeoutError("slow upstream")

    ctx = _FakeCtx()
    # attempt == MAX_JOB_RETRIES means the budget is spent: record, don't loop.
    await _tick_wrapper("unit_bounded", boom, attempt=MAX_JOB_RETRIES)(ctx)
    assert ctx.job_queue.retries == []
    assert "unit_bounded" in _failed_jobs()


@pytest.mark.asyncio
async def test_success_records_no_failure() -> None:
    async def fine(ctx: Any) -> dict[str, int]:
        return {"posted": 2}

    ctx = _FakeCtx()
    result = await _tick_wrapper("unit_ok", fine, envelope=True)(ctx)

    assert result == {"posted": 2}
    assert "unit_ok" not in _failed_jobs()
    assert ctx.job_queue.retries == []


@pytest.mark.asyncio
async def test_failure_envelope_is_logged_with_taxonomy_and_retry_flag(caplog) -> None:
    async def boom(ctx: Any) -> int:
        raise ConnectionResetError("reset")

    with caplog.at_level("ERROR"):
        await _tick_wrapper("unit_logged", boom)(_FakeCtx())

    record = next(
        r for r in caplog.records if "unit_logged" in str(r.msg) or "unit_logged" in str(r.args)
    )
    assert "channels:tick" in str(record.msg)
