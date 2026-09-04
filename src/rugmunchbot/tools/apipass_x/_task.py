"""ApiPass X — createTask → poll → run_task flow.

References the package namespace (``_pkg``) for shared config and the
rate-limiter/key helpers so monkeypatching the package propagates.
"""

from __future__ import annotations

import time
from typing import Any

import rugmunchbot.tools.apipass_x as _pkg


def _build_input(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Input body for createTask — the endpoint's own params, top-level.

    2026-08-29 (verified against the live ApiPass model pages): the ``model``
    field IS the full endpoint path (``twitter/simple/user-tweets``,
    ``twitter/simple/tweet-detail``, ``twitter/simple/search``, …) and the
    ``input`` object carries the endpoint's params directly (``user_id`` /
    ``tweet_id`` / ``q``). The endpoint is NOT nested inside ``input`` — it
    rides in ``model`` (see ``run_task``). ``endpoint`` is kept as a param so
    the usage meter still logs the lane, but it is not sent in the payload.
    """
    del endpoint  # the endpoint path is passed as `model`, not inside `input`
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    return clean


async def create_task(model: str, input_body: dict[str, Any]) -> str | None:
    """Create one ApiPass task; returns ``taskId`` or None (fail-open).

    Applies the hourly budget BEFORE the HTTP call — a call that would
    exceed the burn is not made. Returns None when unconfigured, rate
    limited, or the API rejects the task.
    """
    if not _pkg._resolve_api_key():
        _pkg.logger.debug("apipass_x:create_skipped_no_key")
        return None
    if not _pkg._task_create_allowed():
        return None
    payload = {"model": model, "input": input_body}
    try:
        async with _pkg.httpx.AsyncClient(timeout=_pkg.CREATE_TIMEOUT_S) as client:
            r = await client.post(
                f"{_pkg.APIPASS_BASE_URL}/api/v1/jobs/createTask",
                json=payload,
                headers=_pkg._auth_headers(),
            )
            if r.status_code != 200:
                _pkg.logger.warning(
                    "apipass_x:create_http status=%s err=%s", r.status_code, r.text[:160]
                )
                return None
            body = r.json() or {}
            data = body.get("data") or {}
            task_id = str(data.get("taskId") or "").strip()
            if not task_id:
                _pkg.logger.warning("apipass_x:create_no_task_id body=%s", str(body)[:200])
                return None
            _pkg._note_task_created()
            _pkg._meter_usage(endpoint=model, task_id=task_id)
            _pkg.logger.info("apipass_x:task_created model=%s task=%s", model, task_id)
            return task_id
    except (_pkg.httpx.HTTPError, _pkg.httpx.TimeoutException, ValueError) as exc:
        _pkg.logger.warning("apipass_x:create_failed err=%s", str(exc)[:160])
        return None


async def get_task(task_id: str) -> dict[str, Any] | None:
    """Poll one task record; returns the ``data`` dict or None (fail-open)."""
    if not task_id:
        return None
    try:
        async with _pkg.httpx.AsyncClient(timeout=_pkg.POLL_TIMEOUT_S) as client:
            r = await client.get(
                f"{_pkg.APIPASS_BASE_URL}/api/v1/jobs/recordInfo",
                params={"taskId": task_id},
                headers=_pkg._auth_headers(),
            )
            if r.status_code != 200:
                _pkg.logger.debug("apipass_x:poll_http status=%s task=%s", r.status_code, task_id)
                return None
            body = r.json() or {}
            return body.get("data") if isinstance(body.get("data"), dict) else None
    except (_pkg.httpx.HTTPError, _pkg.httpx.TimeoutException, ValueError) as exc:
        _pkg.logger.debug("apipass_x:poll_failed task=%s err=%s", task_id, str(exc)[:120])
        return None


_TERMINAL_STATES: frozenset[str] = frozenset(
    {"success", "succeeded", "completed", "complete", "done", "finished"}
)
_FAIL_STATES: frozenset[str] = frozenset({"failed", "failure", "error", "canceled", "cancelled"})


async def run_task(
    model: str, input_body: dict[str, Any], *, total_timeout: float | None = None
) -> dict[str, Any] | None:
    """Create + poll until terminal; returns ``resultJson`` dict or None.

    Polling is free (does not burn credits). A task that never reaches a
    terminal state within the total timeout is abandoned (fail-open) — the
    ApiPass credit for a failed task is refunded server-side, and the
    caller simply falls back to the next rail.
    """
    task_id = await _pkg.create_task(model, input_body)
    if not task_id:
        return None
    budget_s = float(total_timeout or _pkg.POLL_TOTAL_S)
    deadline = time.monotonic() + budget_s
    while True:
        await _sleep(_pkg.POLL_INTERVAL_S)
        record = await _pkg.get_task(task_id)
        if record is None:
            if time.monotonic() >= deadline:
                _pkg.logger.warning("apipass_x:task_timeout task=%s", task_id)
                return None
            continue
        state = str(record.get("state") or "").strip().lower()
        if state in _FAIL_STATES:
            _pkg.logger.warning(
                "apipass_x:task_failed task=%s code=%s msg=%s",
                task_id,
                record.get("failCode") or "",
                str(record.get("failMsg") or "")[:200],
            )
            return None
        if state in _TERMINAL_STATES:
            result = record.get("resultJson")
            return result if isinstance(result, dict) else None
        if time.monotonic() >= deadline:
            _pkg.logger.warning("apipass_x:task_timeout task=%s state=%s", task_id, state)
            return None


async def _sleep(seconds: float) -> None:
    """Sleep without importing asyncio at module load (keeps import light)."""
    import asyncio

    await asyncio.sleep(seconds)
