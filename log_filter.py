"""Log filter that redacts Telegram bot tokens from all log output."""

from __future__ import annotations

import logging
import re

# Match bot tokens in URLs: bot<digits>:<alphanumeric>
_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{35,45}")
# Match a BARE token (no "bot" prefix). python-telegram-bot's InvalidToken
# message prints the raw token — ``The token `8720275246:AAH...` was rejected``
# — which the URL-shaped regex above does NOT catch, so a revoked token leaked
# into journald on every crash-loop restart (observed 2026-10-16). Telegram
# bot tokens are exactly ``<8-10 digits>:<35-char base64url>``.
_BARE_TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")


class TokenRedactionFilter(logging.Filter):
    """Redacts Telegram bot tokens from log messages."""

    @staticmethod
    def _scrub(text: str) -> str:
        """Redact both URL-shaped and bare Telegram bot tokens."""
        return _BARE_TOKEN_RE.sub("<REDACTED>", _TOKEN_RE.sub("bot<REDACTED>", text))

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            # Redact tokens in format-string args
            record.args = tuple(
                self._scrub(str(a)) if isinstance(a, str) else a for a in record.args
            )
        return True


def install() -> None:
    """Install the redaction filter on all root handlers + httpx logger."""
    f = TokenRedactionFilter()
    logging.root.addFilter(f)
    # httpx logs URLs containing the token
    logging.getLogger("httpx").addFilter(f)


def suppress_httpx_urls() -> None:
    """Suppress httpx INFO logs that contain bot tokens in URLs."""
    # Set httpx to WARNING level so token URLs never appear
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    # PTB's own httpx usage
    logging.getLogger("telegram.ext._utils").setLevel(logging.WARNING)
    logging.getLogger("telegram.request").setLevel(logging.WARNING)
