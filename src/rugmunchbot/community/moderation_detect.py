"""Detector tables for the moderation core.

Pure text/behaviour classification: no state, no I/O, no Telegram import, so
every detector is exercised by calling :func:`detect_reasons` with a plain
event object.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rugmunchbot.community.moderation import MessageEvent, Policy

MAX_TEXT = 4096

EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
URL_RE = re.compile(r"https?://([^/\s?#]+)([^\s]*)", re.IGNORECASE)

PHISH_PATH = (
    "claim",
    "airdrop",
    "connect",
    "validate",
    "rectify",
    "migrat",
    "redeem",
    "eligib",
    "bonus",
    "whitelist",
)
PHISH_HOST = (
    "drainer",
    "claim-",
    "-claim",
    "airdrop",
    "migrat",
    "rectif",
    "validat",
    "walletconnect",
    "wallet-connect",
    "giveaway",
)
SHORTENERS = frozenset({"bit.ly", "t.co", "tinyurl.com", "cutt.ly", "is.gd", "shorturl.at"})

SHILL_RE = re.compile(
    r"\b(100x|1000x|guaranteed|to the moon|next gem|presale|buy now|don'?t miss"
    r"|easy money|pump incoming|last chance|free money|send sol|send eth)\b",
    re.IGNORECASE,
)


def _trusted(host: str, path: str, trusted_hosts: frozenset[str]) -> bool:
    if not trusted_hosts or not host.endswith(tuple(trusted_hosts)):
        return False
    return not any(token in path for token in PHISH_PATH)


def detect_reasons(ev: MessageEvent, policy: Policy, trusted_hosts: frozenset[str]) -> list[str]:
    """Return every detector hit for *ev*, in stable order."""
    text = (ev.text or "")[:MAX_TEXT]
    reasons: list[str] = []

    for host, path in URL_RE.findall(text):
        host = host.lower().removeprefix("www.")
        path = path.lower()
        if _trusted(host, path, trusted_hosts):
            continue
        if (
            host in SHORTENERS
            or any(t in host for t in PHISH_HOST)
            or any(t in path for t in PHISH_PATH)
        ):
            reasons.append(f"phishing_link:{host}")

    if SHILL_RE.search(text):
        reasons.append("pump_shilling")

    addresses = EVM_RE.findall(text) + SOL_RE.findall(text)
    if len(addresses) >= 2 or (addresses and len(text) < 120):
        reasons.append("wallet_address_spam")

    if len(ev.mentions) > policy.max_mentions:
        reasons.append(f"mention_raid:{len(ev.mentions)}")

    if policy.new_account_days and ev.account_age_days is not None:
        if ev.account_age_days < policy.new_account_days:
            reasons.append(f"new_account:{ev.account_age_days}d")
        if ev.forwarded:
            reasons.append("forward_raid")

    return reasons
