"""Formatter primitives — escaping, truncation, addresses, separators.

``esc`` and ``clip`` are the single canonical helpers for user-controlled
text in a Telegram-HTML card. Every card formatter uses these instead of
its own copy: a rendered value that is not escaped can break the whole
message (Telegram rejects unbalanced/unknown tags), and a value that is not
clipped can push the card past the 4096-character ceiling.
"""

from __future__ import annotations

import re

from rugmunchbot.content_style._escape import esc as _esc

BETA_BANNER = (
    "\U0001f680 <b>FREE PUBLIC BETA</b> \u2014 all premium features "
    "unlocked while we test.\n"
    "\U0001f4c5 <b>Premium plans coming soon</b> \u2014 beta users get "
    "launch discounts.\n"
    "\u2b50 Optional: \U0001f52c <b>Deep Scan</b> buttons run on Telegram "
    "Stars (250\u2605); everything else is free."
)


BETA_STATUS_LINE = "\U0001f680 <b>FREE PUBLIC BETA</b> \u2014 all features unlocked \U0001f389"


BETA_PAYMENTS_LINE = (
    "\U0001f680 <b>In beta</b> \u2014 every feature is free; optional "
    "Telegram Stars purchases (deep scans, top-ups) are live for early supporters."
)


TOKEN_RE = re.compile(r"\$([A-Za-z]{2,10})\b")


EVM_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")


SOL_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def esc(value: object) -> str:
    """Escape *value* for a Telegram HTML text node (canonical helper).

    ``None`` renders as an empty string, never the literal ``"None"``.
    """
    return _esc("" if value is None else value)


def clip(value: object, limit: int) -> str:
    """Truncate *value* to *limit* characters with a single ellipsis.

    Only marks the cut when content was actually dropped, and never splits
    a word mid-way without the marker.
    """
    text = "" if value is None else str(value)
    text = text.strip()
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def is_evm(addr: str) -> bool:
    from rugmunchbot._vendor.shared_utils import is_evm_address

    return is_evm_address(addr)


def is_sol(addr: str) -> bool:
    from rugmunchbot._vendor.shared_utils import is_solana_address

    return is_solana_address(addr)


def detect_chain(addr: str) -> str:
    if is_evm(addr):
        return "ethereum"
    if is_sol(addr):
        return "solana"
    return "unknown"


def short_addr(addr: str) -> str:
    if len(addr) > 12:
        return f"{addr[:6]}...{addr[-4:]}"
    return addr


def sep(char: str = "\u2501", length: int = 28) -> str:
    return char * length


def thin_sep(char: str = "\u2500", length: int = 24) -> str:
    return char * length
