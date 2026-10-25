"""One exception taxonomy: transient vs permanent vs user-error.

The taxonomy decides retry (scheduler), quiet-vs-reply (error handler) and
dead-letter-vs-retry (alert worker). These tests pin the mapping so the three
call sites cannot drift.
"""

from __future__ import annotations

import socket

import httpx
import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from rugmunchbot.errors import (
    BotError,
    ErrorKind,
    PermanentError,
    TransientError,
    UserError,
    backoff_seconds,
    classify,
    is_retryable,
    safe_user_message,
)


class _HttpError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize(
    "exc",
    [
        TimeoutError("slow"),
        TimeoutError(),
        ConnectionResetError("reset"),
        socket.gaierror("dns"),
        httpx.ConnectTimeout("timeout"),
        RetryAfter(3),
        TimedOut("timed out"),
        NetworkError("network"),
        _HttpError(429),
        _HttpError(503),
        _HttpError(0),
        TransientError("upstream blip"),
    ],
)
def test_transient_errors_are_retryable(exc: BaseException) -> None:
    assert classify(exc) is ErrorKind.TRANSIENT
    assert is_retryable(exc) is True


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("bad input"),
        KeyError("missing"),
        TypeError("wrong type"),
        BadRequest("bad request"),
        _HttpError(400),
        _HttpError(404),
        PermanentError("nope"),
        RuntimeError("unknown bug"),
    ],
)
def test_permanent_and_unknown_errors_are_not_retried(exc: BaseException) -> None:
    assert classify(exc) is ErrorKind.PERMANENT
    assert is_retryable(exc) is False


def test_user_error_is_its_own_kind_and_never_retried() -> None:
    exc = UserError("Address is not a valid Solana mint")
    assert classify(exc) is ErrorKind.USER
    assert is_retryable(exc) is False
    assert "not a valid" in safe_user_message(exc)


def test_bot_error_carries_a_machine_code() -> None:
    exc = TransientError("flaky", code="UPSTREAM_503")
    assert exc.code == "UPSTREAM_503"
    assert isinstance(exc, BotError)


def test_backoff_grows_then_caps() -> None:
    delays = [backoff_seconds(n) for n in range(6)]
    assert delays[0] <= delays[2] <= delays[5]
    assert max(delays) <= 300.0


def test_safe_user_message_never_leaks_the_exception_text() -> None:
    secret = "token 8720275246:AAH8ft1O7RfmhX2bn4HMl2rj9eKrkFGS4EU rejected"
    for exc in (ValueError(secret), RuntimeError(secret), ConnectionResetError(secret)):
        msg = safe_user_message(exc)
        assert "AAH8ft1O7" not in msg
        assert "token" not in msg.lower()
