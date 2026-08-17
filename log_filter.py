"""Log filter that redacts Telegram bot tokens from all log output."""

from __future__ import annotations

import logging
import re

# Match bot tokens in URLs: bot<digits>:<alphanumeric>
_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{35,45}")


class TokenRedactionFilter(logging.Filter):
    """Redacts Telegram bot tokens from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, str):
            record.msg = _TOKEN_RE.sub("bot<REDACTED>", record.msg)
        if record.args:
            # Redact tokens in format-string args
            record.args = tuple(
                _TOKEN_RE.sub("bot<REDACTED>", str(a)) if isinstance(a, str) else a
                for a in record.args
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
