"""Log redaction: one place that decides what must never reach a log line.

Covered classes (all found in real leaks from this bot's journal):

* Telegram bot tokens — URL shape (``/bot<id>:<secret>``) and the bare shape
  python-telegram-bot prints in ``InvalidToken`` (2026-10-16 leak).
* Invite links — ``t.me/+hash``, ``t.me/joinchat/...``, ``discord.gg/...``
  (an invite link is a credential: whoever holds it joins the channel).
* Email addresses.
* Wallet secrets — PEM private-key blocks, and any value labelled as a private
  key / mnemonic / seed / API key. Plain 32-byte hex is NOT redacted: it is
  indistinguishable from a transaction hash, and blinding tx hashes makes
  incidents undebuggable.

``scrub`` is pure and total; ``RedactionFilter`` applies it to ``logging``
records and ``RedactionProcessor`` applies it to structlog event dicts.
"""

from __future__ import annotations

import logging
import re
from typing import Any

__all__ = ["RedactionFilter", "RedactionProcessor", "install", "scrub"]

_REDACTED = "<REDACTED>"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # PEM private keys (multi-line: let DOTALL-free [\s\S] do the work).
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "<REDACTED_PRIVATE_KEY>",
    ),
    # Labelled secrets: private_key=…, mnemonic: …, seed phrase …, api_key=…
    (
        re.compile(
            r"(?i)\b(private[ _-]?key|privkey|secret[ _-]?key|mnemonic|seed[ _-]?phrase|"
            r"api[ _-]?key|passphrase)\b(\s*[:=]\s*)(?:['\"][^'\"]{4,}['\"]|[^\s'\"]{4,})"
        ),
        r"\1\2" + _REDACTED,
    ),
    # Authorization headers.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/-]{8,}=*"), r"\1 " + _REDACTED),
    # Telegram bot token in a URL: bot<digits>:<secret>.
    (re.compile(r"bot\d+:[A-Za-z0-9_-]{35,45}"), "bot" + _REDACTED),
    # Bare Telegram bot token (InvalidToken message).
    (re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"), _REDACTED),
    # Invite links — treat as credentials.
    (
        re.compile(r"(?:https?://)?t\.me/(?:\+|joinchat/)[A-Za-z0-9_-]+"),
        "<REDACTED_INVITE>",
    ),
    (
        re.compile(r"(?:https?://)?(?:discord(?:app)?\.com/invite|discord\.gg)/[A-Za-z0-9-]+"),
        "<REDACTED_INVITE>",
    ),
    # Email addresses.
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b"),
        "<REDACTED_EMAIL>",
    ),
)


def scrub(text: str) -> str:
    """Return *text* with every secret/PII class replaced by a marker."""
    if not text:
        return text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub_value(v) for v in value)
    return value


class RedactionFilter(logging.Filter):
    """Scrub ``logging`` records (message, format args and exception text)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _scrub_value(v) for k, v in record.args.items()}
            else:
                record.args = tuple(
                    _scrub_value(a) if isinstance(a, (str, dict, list, tuple)) else a
                    for a in record.args
                )
        if record.exc_text:
            record.exc_text = scrub(record.exc_text)
        return True


class RedactionProcessor:
    """structlog processor: scrub every string in the event dict."""

    def __call__(self, logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        return {key: _scrub_value(value) for key, value in event_dict.items()}


def install() -> None:
    """Install redaction on the logging root (idempotent)."""
    root = logging.getLogger()
    if any(isinstance(f, RedactionFilter) for f in root.filters):
        return
    root.addFilter(RedactionFilter())
