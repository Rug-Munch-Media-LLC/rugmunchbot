"""Token redaction must cover the BARE token form, not just URLs.

Regression (2026-10-16): python-telegram-bot's ``InvalidToken`` message prints
the raw token — ``The token `8720275246:AAH...` was rejected by the server.``
— with no ``bot`` prefix. The original filter only matched ``bot<digits>:...``
(URL shape), so a revoked token leaked into journald on every crash-loop
restart. The filter must redact both shapes.
"""

from __future__ import annotations

import logging

from log_filter import TokenRedactionFilter

# A syntactically valid Telegram bot token (digits:35-char base64url).
_BARE = "8720275246:AAH8ft1O7RfmhX2bn4HMl2rj9eKrkFGS4EU"


def _record(msg: str) -> logging.LogRecord:
    return logging.LogRecord("t", logging.ERROR, "x", 1, msg, None, None)


def test_bare_token_is_redacted():
    rec = _record(f"The token `{_BARE}` was rejected by the server.")
    TokenRedactionFilter().filter(rec)
    assert _BARE not in rec.msg
    assert "<REDACTED>" in rec.msg


def test_url_shaped_token_is_redacted():
    rec = _record(f"GET https://api.telegram.org/bot{_BARE}/getMe")
    TokenRedactionFilter().filter(rec)
    assert _BARE not in rec.msg
    assert "bot<REDACTED>" in rec.msg


def test_token_in_format_args_is_redacted():
    rec = logging.LogRecord("t", logging.ERROR, "x", 1, "token=%s", (_BARE,), None)
    TokenRedactionFilter().filter(rec)
    assert _BARE not in str(rec.args)
