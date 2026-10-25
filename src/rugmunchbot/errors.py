"""Exception taxonomy for RugMunchBot.

Three kinds, and only three:

* ``TRANSIENT`` — retrying may succeed (timeout, 429, 5xx, connection reset).
* ``PERMANENT`` — retrying cannot succeed (bad input, 404, unformattable item).
* ``USER``      — the *operator/user* asked for something invalid (validation).

Every classification decision lives here so handlers, the scheduler and the
alert worker cannot drift apart. ``unknown`` maps to ``PERMANENT``: never start
a retry storm on an error we do not understand.
"""

from __future__ import annotations

import asyncio
import random
import socket
from enum import StrEnum

__all__ = [
    "BotError",
    "ErrorKind",
    "PermanentError",
    "TransientError",
    "UserError",
    "backoff_seconds",
    "classify",
    "is_retryable",
    "safe_user_message",
]


class ErrorKind(StrEnum):
    """Retry semantics for an exception."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    USER = "user"


class BotError(Exception):
    """Base for every error the bot raises deliberately."""

    kind: ErrorKind = ErrorKind.PERMANENT
    code: str = "BOT_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code


class TransientError(BotError):
    """Retry with backoff."""

    kind = ErrorKind.TRANSIENT
    code = "TRANSIENT"


class PermanentError(BotError):
    """Do not retry — the same call will fail the same way."""

    kind = ErrorKind.PERMANENT
    code = "PERMANENT"


class UserError(BotError):
    """Invalid request from the user — report it, never retry, never alert."""

    kind = ErrorKind.USER
    code = "USER_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message, code=code or "USER_ERROR")
        self.user_message = message


def _telegram_kind(exc: BaseException) -> ErrorKind | None:
    """Classify python-telegram-bot errors (import kept local: optional dep)."""
    try:
        from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
    except ImportError:  # pragma: no cover - telegram is a hard dep in prod
        return None
    # PTB v22 makes BadRequest a subclass of NetworkError, so the permanent
    # checks MUST come first or every bad request (chat not found, message too
    # long) is silently classified transient and retried forever.
    if isinstance(exc, (BadRequest, Forbidden)):
        return ErrorKind.PERMANENT
    if isinstance(exc, (RetryAfter, TimedOut, NetworkError)):
        return ErrorKind.TRANSIENT
    return None


def _http_status_kind(exc: BaseException) -> ErrorKind | None:
    """Classify backend/HTTP errors by status code."""
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if not isinstance(status, int):
        return None
    if status == 429:
        return ErrorKind.TRANSIENT
    if status >= 500 or status == 0:
        return ErrorKind.TRANSIENT
    if 400 <= status < 500:
        return ErrorKind.PERMANENT
    return None


def classify(exc: BaseException) -> ErrorKind:
    """Return the retry semantics of *exc* (see module docstring)."""
    if isinstance(exc, BotError):
        return exc.kind
    kind = _telegram_kind(exc)
    if kind is not None:
        return kind
    kind = _http_status_kind(exc)
    if kind is not None:
        return kind
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, socket.gaierror)):
        return ErrorKind.TRANSIENT
    try:
        import httpx

        if isinstance(exc, httpx.TransportError):
            return ErrorKind.TRANSIENT
    except ImportError:  # pragma: no cover
        pass
    # Programming errors and malformed data: retrying changes nothing.
    if isinstance(exc, (ValueError, TypeError, KeyError, AttributeError, LookupError)):
        return ErrorKind.PERMANENT
    return ErrorKind.PERMANENT


def is_retryable(exc: BaseException) -> bool:
    """True when retrying *exc* is allowed by policy."""
    return classify(exc) is ErrorKind.TRANSIENT


def backoff_seconds(
    attempt: int, *, base: float = 30.0, cap: float = 300.0, jitter: float = 5.0
) -> float:
    """Exponential backoff with jitter for retry *attempt* (0-based)."""
    delay = min(base * (2 ** max(0, attempt)), cap)
    return min(delay + random.uniform(0, jitter), cap)  # noqa: S311 - retry jitter, not crypto


def safe_user_message(exc: BaseException, error_id: str = "") -> str:
    """A message safe to send to a user: never contains ``str(exc)``."""
    kind = classify(exc)
    if kind is ErrorKind.TRANSIENT:
        return "Temporary hiccup -- try again in a minute."
    if kind is ErrorKind.USER:
        detail = getattr(exc, "user_message", "")
        return str(detail) if detail else "That request is not valid."
    suffix = f" (error {error_id})" if error_id else ""
    return (
        f"Something went wrong{suffix}. Try again -- if it repeats, send the error ID to support."
    )
