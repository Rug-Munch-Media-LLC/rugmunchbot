# VENDORED 2026-08-17 from rmi-backend/app/core/circuit_breaker.py (frozen copy; do not edit).
# Re-vendor: bash scripts/refresh-vendors.sh
# ---
"""
Enhanced Async Circuit Breaker for external API calls.

Prevents cascading failures when external services degrade.
States: CLOSED (normal) → OPEN (failing, reject fast) → HALF_OPEN (test recovery) → CLOSED.

Enhancements over v1:
  - asyncio.Lock for thread-safety in async context
  - Stale-cache serving — returns last-known-good result during OPEN state
  - 4xx vs 5xx distinction: only trip on 5xx / timeouts / connection errors, not 4xx
  - ``wrap()`` decorator for easy per-method protection
  - Stats tracking for observability

Usage — inline (recommended for connectors with own caching):
    _cb = AsyncCircuitBreaker("coingecko")

    async def _get(self, ...):
        if not await _cb.can_call():
            stale = self._cache.get(key)  # serve stale if we have it
            if stale is not None:
                return stale
            raise CircuitBreakerOpenError(...)
        try:
            r = await client.get(...)
            if r.status_code == 200:
                _cb.set_stale_result(data)
                await _cb.record_success()
                return data
            elif r.status_code >= 500:
                await _cb.record_failure(is_server_error=True)
            else:
                await _cb.record_failure(is_server_error=False)
        except (ConnectionError, OSError, TimeoutError):
            await _cb.record_failure(is_server_error=True)
            raise

Usage — decorator (for simple wrappers):
    _cb = AsyncCircuitBreaker("simple")

    @_cb.wrap()
    async def fetch(self, ...):
        ...

Usage — call():
    result = await _cb.call(lambda: client.get(url))

Usage — context manager:
    async with _cb:
        return await client.get(url)
"""

from __future__ import annotations

import asyncio
import functools
import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit is OPEN and no stale cache is available."""

    def __init__(self, name: str, state: State, message: str | None = None) -> None:
        self.name = name
        self.state = state
        msg = message or f"Circuit breaker '{name}' is {state.value} — call rejected"
        super().__init__(msg)


class AsyncCircuitBreaker:
    """Async circuit breaker with failure tracking, stale-cache serving, and 4xx/5xx distinction.

    Key features:
    - In-memory sliding-window failure counting (no Redis dependency)
    - Stale result caching — serve last-known-good result during OPEN state
    - 4xx distinction: only trip on 5xx / timeouts / connection errors, not client errors
    - Three usage patterns: ``call()``, context manager ``async with``, and ``wrap()`` decorator
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        failure_window: float = 60.0,
        recovery_timeout: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.failure_window = failure_window
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max

        self._state = State.CLOSED
        self._failures: list[float] = []
        self._opened_at: float = 0.0
        self._half_open_count = 0
        self._lock = asyncio.Lock()

        # Stale cache — last-known-good result
        self._stale_result: Any = None
        self._stale_result_time: float = 0.0
        self._stale_max_age: float = 300.0  # 5 minutes default

        # Stats
        self._total_calls = 0
        self._rejected_calls = 0
        self._successful_calls = 0

    # ── Public state inspection ────────────────────────────────────────────

    @property
    def state(self) -> State:
        return self._state

    def stats(self) -> dict[str, Any]:
        """Return snapshot of breaker metrics for observability."""
        return {
            "name": self.name,
            "state": self._state.value,
            "total_calls": self._total_calls,
            "rejected_calls": self._rejected_calls,
            "successful_calls": self._successful_calls,
            "failure_count": len(self._failures),
        }

    # ── Stale-cache helpers (sync — called after the result is parsed) ──────

    def set_stale_result(self, result: Any) -> None:
        """Store the last-known-good response for stale serving during OPEN state."""
        self._stale_result = result
        self._stale_result_time = time.monotonic()

    def get_stale_result(self, max_age: float | None = None) -> Any | None:
        """Return stale result if it exists and hasn't expired."""
        if self._stale_result is not None:
            age = time.monotonic() - self._stale_result_time
            if age < (max_age or self._stale_max_age):
                return self._stale_result
        return None

    # ── Core async state management ────────────────────────────────────────

    async def _prune_failures(self) -> None:
        """Remove failures that fell outside the sliding window."""
        now = time.monotonic()
        cutoff = now - self.failure_window
        self._failures = [t for t in self._failures if t > cutoff]

    async def _transition(self, new_state: State) -> None:
        """Record a state transition and log it."""
        old = self._state
        self._state = new_state
        if new_state == State.OPEN:
            self._opened_at = time.monotonic()
        logger.warning(
            "circuit_breaker.transition breaker=%s from=%s to=%s",
            self.name,
            old.value,
            new_state.value,
        )

    # ── Public check / record API ──────────────────────────────────────────

    async def can_call(self) -> bool:
        """Return True if the circuit allows an execution attempt.

        * CLOSED -> True (prunes old failures)
        * HALF_OPEN -> True (allows probe)
        * OPEN -> transitions to HALF_OPEN if recovery timeout elapsed, else False
        """
        async with self._lock:
            if self._state == State.CLOSED:
                await self._prune_failures()
                return True

            if self._state == State.HALF_OPEN:
                return self._half_open_count < self.half_open_max

            # OPEN — check if recovery timeout has elapsed
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                await self._transition(State.HALF_OPEN)
                self._half_open_count = 0
                return True

            return False

    async def record_success(self) -> None:
        """Record a successful call. Resets failure count.
        If HALF_OPEN, transitions back to CLOSED.
        """
        async with self._lock:
            self._total_calls += 1
            self._successful_calls += 1
            self._failures.clear()
            self._half_open_count = 0
            if self._state != State.CLOSED:
                await self._transition(State.CLOSED)

    async def record_failure(self, is_server_error: bool = True) -> None:
        """Record a failed call.

        Args:
            is_server_error:
                True  — 5xx, timeout, connection error -> counts toward OPEN threshold
                False — 4xx (client error, rate limit)   -> *NOT* counted
        """
        if not is_server_error:
            return  # 4xx errors don't trip the breaker

        async with self._lock:
            self._total_calls += 1
            self._failures.append(time.monotonic())
            await self._prune_failures()

            failure_count = len(self._failures)
            if failure_count >= self.failure_threshold:
                await self._transition(State.OPEN)

    # ── Usage pattern 1: call() — wrap a single coroutine ──────────────────

    async def call(self, coro_factory: Callable[[], Any], stale_ok: bool = True) -> Any:
        """Execute *coro_factory()* under circuit breaker protection.

        Args:
            coro_factory: Zero-argument callable that returns an awaitable.
            stale_ok: If True and circuit is OPEN, serve stale result if available.

        Returns:
            Result of *coro_factory()*, or stale cached result.

        Raises:
            CircuitBreakerOpenError: If OPEN and no stale result is available.
        """
        if not await self.can_call():
            if stale_ok:
                stale = self.get_stale_result()
                if stale is not None:
                    return stale
            raise CircuitBreakerOpenError(self.name, self._state)

        if self._state == State.HALF_OPEN:
            self._half_open_count += 1

        try:
            result = await coro_factory()
            await self.record_success()
            return result
        except (ConnectionError, OSError, TimeoutError):
            await self.record_failure(is_server_error=True)
            raise

    # ── Usage pattern 2: wrap() decorator ──────────────────────────────────

    def wrap(self, stale_ok: bool = True) -> Any:
        """Decorator that wraps an async method with circuit-breaker protection.

        .. caution::
           Because the decorator sees whatever the method raises, it cannot
           distinguish 4xx from 5xx.  If you need that (and you should),
           use the inline ``can_call()`` / ``record_success()`` /
           ``record_failure(is_server_error=)`` pattern instead.
        """
        breaker = self

        def decorator(func: F) -> F:
            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                if not await breaker.can_call():
                    if stale_ok:
                        stale = breaker.get_stale_result()
                        if stale is not None:
                            return stale
                    raise CircuitBreakerOpenError(breaker.name, breaker._state)
                try:
                    result = await func(*args, **kwargs)
                    await breaker.record_success()
                    return result
                except (ConnectionError, OSError, TimeoutError):
                    await breaker.record_failure(is_server_error=True)
                    raise

            return wrapper  # type: ignore[return-value]

        return decorator

    # ── Usage pattern 3: async context manager ─────────────────────────────

    async def __aenter__(self) -> AsyncCircuitBreaker:
        if not await self.can_call():
            raise CircuitBreakerOpenError(self.name, self._state)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> bool:
        if exc_type is None:
            await self.record_success()
        elif exc_type is CircuitBreakerOpenError:
            pass  # Already handled, don't re-record
        elif exc_type is not None:
            await self.record_failure(is_server_error=True)
        return False  # Don't suppress exceptions


class SimpleCircuitBreaker:
    """Thread-safe sync circuit breaker for connectors not yet migrated to async.

    Drop-in for AsyncCircuitBreaker in sync contexts.  Same state machine,
    same stale-cache logic, uses ``threading.Lock`` instead of ``asyncio.Lock``.

    Usage:
        _cb = SimpleCircuitBreaker("defillama")

        def _get(...):
            if not _cb.can_call():
                stale = _cb.get_stale_result()
                if stale: return stale
                raise CircuitBreakerOpenError(...)
            try:
                ...
                _cb.set_stale_result(data)
                _cb.record_success()
            except (ConnectionError, OSError, TimeoutError):
                _cb.record_failure(is_server_error=True)

    .. note::
       Marked for removal when all connectors are async.  Do not add new sync
       methods that depend on this — just use AsyncCircuitBreaker.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        failure_window: float = 60.0,
        recovery_timeout: float = 30.0,
        half_open_max: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.failure_window = failure_window
        self.recovery_timeout = recovery_timeout
        self.half_open_max = half_open_max

        self._state = State.CLOSED
        self._failures: list[float] = []
        self._opened_at: float = 0.0
        self._half_open_count = 0
        self._lock = threading.Lock()

        self._stale_result: Any = None
        self._stale_result_time: float = 0.0
        self._stale_max_age: float = 300.0

    @property
    def state(self) -> State:
        return self._state

    def set_stale_result(self, result: Any) -> None:
        self._stale_result = result
        self._stale_result_time = time.monotonic()

    def get_stale_result(self, max_age: float | None = None) -> Any | None:
        if self._stale_result is not None:
            age = time.monotonic() - self._stale_result_time
            if age < (max_age or self._stale_max_age):
                return self._stale_result
        return None

    def _prune_failures(self) -> None:
        now = time.monotonic()
        cutoff = now - self.failure_window
        self._failures = [t for t in self._failures if t > cutoff]

    def _transition(self, new_state: State) -> None:
        old = self._state
        self._state = new_state
        if new_state == State.OPEN:
            self._opened_at = time.monotonic()
        logger.warning(
            "circuit_breaker.transition breaker=%s from=%s to=%s",
            self.name,
            old.value,
            new_state.value,
        )

    def can_call(self) -> bool:
        with self._lock:
            if self._state == State.CLOSED:
                self._prune_failures()
                return True
            if self._state == State.HALF_OPEN:
                return self._half_open_count < self.half_open_max
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._transition(State.HALF_OPEN)
                self._half_open_count = 0
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures.clear()
            self._half_open_count = 0
            if self._state != State.CLOSED:
                self._transition(State.CLOSED)

    def record_failure(self, is_server_error: bool = True) -> None:
        if not is_server_error:
            return
        with self._lock:
            self._failures.append(time.monotonic())
            self._prune_failures()
            if len(self._failures) >= self.failure_threshold:
                self._transition(State.OPEN)
