"""Log filter that redacts secrets/PII from all log output.

Thin adapter over the canonical patterns in
``rugmunchbot.support.redaction`` (single source of truth): Telegram bot
tokens, invite links, emails, PEM private keys and labelled wallet secrets.
Kept as a top-level module because ``secure_launcher.py`` installs it before
the bot package's own logging setup runs, and older callers/tests import
``TokenRedactionFilter`` from here.
"""

from __future__ import annotations

import logging
import re

from rugmunchbot.support.redaction import RedactionFilter as _RedactionFilter, scrub

# Match bot tokens in URLs: bot<digits>:<alphanumeric>
_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]{35,45}")
# Bare token (no "bot" prefix) — python-telegram-bot's InvalidToken message
# prints the raw token, which the URL-shaped regex does NOT catch (leak
# observed 2026-10-16).
_BARE_TOKEN_RE = re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")


class TokenRedactionFilter(_RedactionFilter):
    """Backwards-compatible name; now scrubs every class, not just tokens."""

    @staticmethod
    def _scrub(text: str) -> str:
        """Redact both URL-shaped and bare Telegram bot tokens (+ all classes)."""
        return scrub(_BARE_TOKEN_RE.sub("<REDACTED>", _TOKEN_RE.sub("bot<REDACTED>", text)))


def suppress_httpx_urls() -> None:
    """Keep httpx from logging request URLs (they can carry tokens)."""
    logging.getLogger("httpx").setLevel(logging.WARNING)


def install() -> None:
    """Install the redaction filter on all root handlers + httpx logger."""
    logging.root.addFilter(TokenRedactionFilter())
    logging.getLogger("httpx").addFilter(TokenRedactionFilter())
    suppress_httpx_urls()
