"""Zero-fabrication guardrail — strips/rejects token symbols not from tools.

Enforces the hard requirement: the bot must NEVER invent a token ticker,
price, market cap, holder count, or risk score. Every factual market claim
must come from a real API call (a tool result).

Two layers:
  1. Prompt layer (see ``_llm.py`` / ``prompts.py``): the system prompt tells
     the LLM to only cite symbols/addresses present in the LIVE DATA blocks.
  2. Post-processing layer (this module): after the LLM answers, we extract
     every ``$SYMBOL`` and on-chain address the answer mentions and verify it
     against the sets the tools actually returned. Anything NOT in those sets
     is fabricated and gets stripped/rejected here — no exceptions.

The ``sanitize_answer`` function is pure and unit-testable.
"""

from __future__ import annotations

import re

# $SYMBOL mentions (uppercase 2-12 LETTERS, e.g. $BONK, $PEPE) — the ticker
# form LLMs most commonly fabricate. Letters only: a leading "$" followed by
# digits is a USD amount ("$50 million"), never a ticker, and the intent
# router (formatters._base.SYMBOL_RE) only recognizes letters-only tickers —
# the guardrail must never strip something the router would not treat as one.
TOKEN_RE = re.compile(r"\$([A-Za-z]{2,12})\b")
# EVM / Solana on-chain addresses.
EVM_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
SOL_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")

NO_LIVE_DATA_MESSAGE = "I don't have live data for that right now."


def extract_symbols(text: str) -> list[str]:
    """Return all ``$SYMBOL`` tickers mentioned in ``text`` (upper-cased)."""
    return [m.group(1).upper() for m in TOKEN_RE.finditer(text)]


def extract_addresses(text: str) -> list[str]:
    """Return all EVM/Solana-shaped addresses in ``text`` (deduplicated)."""
    found: list[str] = []
    for m in EVM_RE.finditer(text):
        found.append(m.group(0))
    for m in SOL_RE.finditer(text):
        candidate = m.group(0)
        if candidate not in found:
            found.append(candidate)
    return found


def sanitize_answer(
    answer: str,
    known_symbols: set[str] | None = None,
    known_addresses: set[str] | None = None,
) -> tuple[str, list[str], list[str]]:
    """Strip fabricated token symbols / addresses from an LLM answer.

    Args:
        answer: The raw LLM output.
        known_symbols: Symbols the tools actually returned (upper-cased set).
        known_addresses: Addresses the tools actually returned.

    Returns:
        ``(sanitized_answer, stripped_symbols, stripped_addresses)``.
        Unknown ``$SYMBOL`` mentions are removed from the text; unknown
        addresses are replaced with a placeholder. If the answer contained
        nothing fabricated the text is returned unchanged.
    """
    known_symbols = known_symbols or set()
    known_addresses = known_addresses or set()

    stripped_symbols: list[str] = []
    for sym in extract_symbols(answer):
        if sym not in known_symbols:
            if sym not in stripped_symbols:
                stripped_symbols.append(sym)
            # Remove the fabricated $SYMBOL token (keep surrounding prose).
            # Case-insensitive: the answer may write $pepe while the known
            # set is upper-cased — a case-sensitive sub would report the
            # symbol as stripped while leaving the fabrication visible.
            answer = re.sub(rf"\${re.escape(sym)}\b", "", answer, flags=re.IGNORECASE)

    stripped_addresses: list[str] = []
    for addr in extract_addresses(answer):
        if addr not in known_addresses:
            if addr not in stripped_addresses:
                stripped_addresses.append(addr)
            answer = answer.replace(addr, "[address not verified]")

    # Collapse double spaces / stray "$" left by removal.
    answer = re.sub(r"\s{2,}", " ", answer).replace("$ ", "$").strip()
    return answer, stripped_symbols, stripped_addresses


def fabrication_note(stripped_symbols: list[str], stripped_addresses: list[str]) -> str:
    """Honest note appended when the guardrail stripped fabricated claims."""
    parts: list[str] = []
    if stripped_symbols:
        unique = [s for s in dict.fromkeys(stripped_symbols) if s][:5]  # dedup, cap at 5
        tickers = ", ".join(f"${s}" for s in unique)
        if tickers:
            parts.append(f"some token symbols ({tickers}) were not verified against live data and were removed")
    if stripped_addresses:
        parts.append("some addresses were not verified against live data")
    if not parts:
        return ""
    return "\n\n<i>Note: " + "; ".join(parts) + ". I don't have live data for those.</i>"
