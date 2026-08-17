"""Shared tunables + input validators for the OSINT command handlers."""

from __future__ import annotations

import re

from app.domains.telegram.rugmunchbot.support.logging import get_logger

logger = get_logger("rmi_bot.osint")

#: Max findings rendered inline; the rest collapse to "+N more…".
_MAX_FINDINGS = 8
#: Max username accounts rendered inline before "+N more…".
_MAX_SITES = 10

#: Domain-ish arg detection for /investigate routing (see cmd_investigate).
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$", re.IGNORECASE)

#: Per-lookup wall-clock budgets (seconds) passed to each OSINT module.
_TIMEOUTS: dict[str, int] = {
    "username": 25,
    "wallet": 45,
    "domain": 45,
    "email": 30,
    "phone": 20,
}

#: Category → emoji for a scannable findings list.
_CATEGORY_EMOJI: dict[str, str] = {
    "chain": "\u26d3\ufe0f",
    "entity": "\U0001f3f7\ufe0f",
    "exchange": "\U0001f3e6",
    "sanction": "\U0001f6ab",
    "scam": "\u26a0\ufe0f",
    "registrant": "\U0001f3e2",
    "registrar": "\U0001f3e2",
    "dns": "\U0001f310",
    "subdomain": "\U0001f9e9",
    "ssl": "\U0001f512",
    "archive": "\U0001f4e6",
    "fingerprint": "\U0001f5a5\ufe0f",
    "breach": "\U0001f573\ufe0f",
    "account": "\U0001f464",
    "reputation": "\u2b50",
    "disposable": "\U0001f5d1\ufe0f",
    "validation": "\u2705",
    "region": "\U0001f4cd",
    "carrier": "\U0001f4f1",
    "risk": "\u26a0\ufe0f",
    "timezone": "\U0001f550",
}

#: Confidence → emoji.
_CONFIDENCE_EMOJI: dict[str, str] = {
    "high": "\U0001f7e2",
    "medium": "\U0001f7e1",
    "low": "\U0001f534",
}

#: Reverse-image-search landing pages surfaced by /face.
_REVERSE_SEARCH_LINKS: list[tuple[str, str]] = [
    ("PimEyes", "https://pimeyes.com"),
    ("Google Lens", "https://lens.google.com"),
    ("Yandex Images", "https://yandex.com/images/"),
]

#: Max aggregate findings rendered inline; the rest collapse to "+N more…".
_MAX_AGGREGATE_FINDINGS = 8
#: Max per-module errors surfaced in the footer note.
_MAX_AGGREGATE_ERRORS = 2
#: Wall-clock budget for the whole aggregate run (shared by every module).
_AGGREGATE_TIMEOUT = 90

#: Valid explicit ``type`` values accepted by /osintall (mirrors engine).
_OSINT_TYPES = ("username", "email", "domain", "wallet", "phone", "face")


# ─── Input validation ────────────────────────────────────────────────


def _looks_like_domain(value: str) -> bool:
    """True when a command arg looks like a domain (has a dot, no spaces)."""
    v = value.strip().lower()
    return bool(_DOMAIN_RE.match(v))


def _valid_username(value: str) -> bool:
    """Mirror ``app.osint_tools`` constraints: 1-128 chars, no whitespace/path separators."""
    return bool(re.match(r"^[^\s/\\]{1,128}$", value)) and "@" not in value


def _valid_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def _valid_phone(value: str) -> bool:
    return bool(re.match(r"^\+?[0-9][0-9\s().-]{4,19}$", value))
