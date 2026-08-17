"""Shared free-text classification primitives for the RugMunchBot.

Canonical home for the classification helpers used by BOTH the tool-intent
router (``tools/intent.py``) and the chat question classifier
(``handlers/chat_ai/_classify.py``):

  * address detection (EVM ``0x...`` / Solana base58) — two documented
    variants: ``extract_addresses`` (uncapped, deduplicated — the router
    variant: every on-chain target the user typed is considered) and
    ``_extract_addresses`` (capped at ``MAX_ADDRESS_HINTS`` with a
    digit/uppercase plausibility gate — the chat-pipeline variant so one
    message cannot trigger a scan storm).
  * symbol detection (``$SYMBOL``) — ``has_symbol`` / ``symbols_in``.

The regexes are the SAME constants the guardrail layer uses (``guardrails`` /
``formatters._base``) so the router, the chat classifier and the fabrication
guardrail can never drift apart again. Pure regex/string logic — no
``Update``/``Context`` state, no handler registration, no chat imports that
would create an import cycle.
"""

from __future__ import annotations

from app.domains.telegram.rugmunchbot.formatters._base import TOKEN_RE as SYMBOL_RE
from app.domains.telegram.rugmunchbot.tools.guardrails import EVM_RE, SOL_RE

# cap address-triggered scans per message
MAX_ADDRESS_HINTS = 2


def _plausible_solana(addr: str) -> bool:
    """Base58 address-likeness: real Solana addresses almost always mix digits
    or uppercase with lowercase, so this filters word-shaped false positives
    ("supercalifragilisticexpialidocious") without dropping genuine tokens."""
    return any(ch.isdigit() or ch.isupper() for ch in addr)


def extract_addresses(text: str) -> list[str]:
    """Return ALL EVM/Solana addresses in ``text`` (uncapped, deduplicated).

    Router variant: every address the user typed is returned so the intent
    router can decide which address-triggered tools (scanner_intel,
    security_scan, wallet_analysis, top_traders) are relevant. Deliberately
    NO plausibility gate — a word-shaped base58 run still counts here because
    routing must never miss a potential on-chain target.
    """
    found: list[str] = []
    for m in EVM_RE.finditer(text):
        found.append(m.group(0))
    for m in SOL_RE.finditer(text):
        if m.group(0) not in found:
            found.append(m.group(0))
    return found


def _extract_addresses(text: str) -> list[str]:
    """Return token addresses (EVM 0x... / Solana base58) found in free text.

    Chat-pipeline variant: capped at ``MAX_ADDRESS_HINTS`` so a single
    message cannot trigger a scan storm. Solana matches that overlap an EVM
    match are skipped, and word-shaped false positives are filtered by the
    digit/uppercase plausibility gate.
    """
    addresses: list[str] = []
    for m in EVM_RE.finditer(text):
        addresses.append(m.group(0))
        if len(addresses) >= MAX_ADDRESS_HINTS:
            return addresses
    for m in SOL_RE.finditer(text):
        addr = m.group(0)
        if addr not in addresses and _plausible_solana(addr):
            addresses.append(addr)
        if len(addresses) >= MAX_ADDRESS_HINTS:
            break
    return addresses


def has_symbol(text: str) -> bool:
    """True when ``text`` contains a ``$SYMBOL``-style token reference."""
    return bool(SYMBOL_RE.search(text))


def symbols_in(text: str) -> list[str]:
    """Return the uppercased ``$SYMBOL`` references found in ``text``."""
    return [m.group(1).upper() for m in SYMBOL_RE.finditer(text)]


__all__ = [
    "MAX_ADDRESS_HINTS",
    "SYMBOL_RE",
    "_extract_addresses",
    "extract_addresses",
    "has_symbol",
    "symbols_in",
]
