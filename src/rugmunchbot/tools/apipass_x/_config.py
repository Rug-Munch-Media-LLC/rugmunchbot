"""ApiPass X — key resolution + budget/rate limiter.

References the package namespace (``_pkg``) for all shared config and mutable
state so that monkeypatching ``rugmunchbot.tools.apipass_x`` (the package)
propagates to the code that reads it.
"""

from __future__ import annotations

import subprocess
import time

import rugmunchbot.tools.apipass_x as _pkg


def _task_creates_this_hour() -> int:
    now = time.monotonic()
    while _pkg._task_created_at and now - _pkg._task_created_at[0] >= 3600.0:
        _pkg._task_created_at.popleft()
    return len(_pkg._task_created_at)


def _task_creates_this_day() -> int:
    now = time.monotonic()
    while _pkg._day_created_at and now - _pkg._day_created_at[0] >= 86400.0:
        _pkg._day_created_at.popleft()
    return len(_pkg._day_created_at)


def task_creates_remaining() -> int:
    """Task-creation calls left in the current hour window (0..MAX)."""
    if _pkg.MAX_CALLS_PER_HOUR <= 0:
        return 0
    return max(0, _pkg.MAX_CALLS_PER_HOUR - _task_creates_this_hour())


def _task_create_allowed() -> bool:
    if _pkg.MAX_CALLS_PER_HOUR <= 0:
        _pkg.logger.debug("apipass_x:disabled_by_budget max_calls_per_hour=0")
        return False
    if _task_creates_this_hour() >= _pkg.MAX_CALLS_PER_HOUR:
        _pkg.logger.warning(
            "apipass_x:rate_limited used=%s max=%s",
            _task_creates_this_hour(),
            _pkg.MAX_CALLS_PER_HOUR,
        )
        return False
    if _pkg.MAX_CALLS_PER_DAY > 0 and _task_creates_this_day() >= _pkg.MAX_CALLS_PER_DAY:
        _pkg.logger.warning(
            "apipass_x:rate_limited_day used=%s max=%s",
            _task_creates_this_day(),
            _pkg.MAX_CALLS_PER_DAY,
        )
        return False
    return True


def _note_task_created() -> None:
    _pkg._task_created_at.append(time.monotonic())
    _pkg._day_created_at.append(time.monotonic())


def _meter_usage(endpoint: str = "", task_id: str = "") -> None:
    """Record one task creation in the credit ledger (fail-open).

    Writes to ``db.kol.record_apipass_usage`` so the 50-free-credit budget
    is tracked (see ``db.kol.apipass_usage_summary``). Never raises — a DB
    that is unavailable must not break the ApiPass rail.
    """
    try:
        from rugmunchbot.db import record_apipass_usage

        record_apipass_usage(
            endpoint=endpoint,
            model=_pkg.APIPASS_TWITTER_MODEL,
            credits=_pkg.CREDITS_PER_CALL,
            task_id=task_id,
            status="created",
        )
    except Exception:  # fail-open by contract
        _pkg.logger.debug("apipass_x:meter_failed endpoint=%s", endpoint)


def _resolve_api_key() -> str:
    """ApiPass key: env ``APIPASS_API_KEY`` → key file
    ``$RMI_SECRETS_DIR/apipass_api_key`` → secrets manager.

    UNCACHED so a key added while the bot runs is picked up on the next tick
    without a restart. Returns "" when none is configured — callers fail
    open (SearXNG fallback rail).
    """
    for key in _pkg._ENV_KEYS:
        value = _pkg.os.getenv(key, "").strip()
        if value:
            return value
    # Fleet secret-store file (world-readable secret dir, mode 0600).
    try:
        with open(_pkg._KEY_FILE, encoding="utf-8") as fh:
            value = fh.read().strip()
        if value:
            return value
    except (OSError, UnicodeDecodeError) as e:
        _pkg.logger.warning("apipass_x:key_file_unreadable path=%s err=%s", _pkg._KEY_FILE, e)
    try:
        value = (
            subprocess.check_output(  # noqa: S603 — gopass is the canonical secret store
                ["gopass", "show", _pkg._GOPASS_PATH],  # noqa: S607 — fixed tool, arg is a secret name
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
            )
            .decode()
            .strip()
        )
        if value:
            return value
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        _pkg.logger.debug("apipass_x:gopass_unavailable err=%s", e)
    return ""


def api_key_configured() -> bool:
    """True when an ApiPass key is available (env or gopass)."""
    return bool(_resolve_api_key())


def _auth_headers() -> dict[str, str]:
    key = _resolve_api_key()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}
