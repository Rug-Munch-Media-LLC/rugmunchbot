"""Parse a `/call` argument list into a token address + chain. No network."""

from __future__ import annotations

import re

_EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SOL = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SYMBOL = re.compile(r"^\$?[A-Za-z0-9][A-Za-z0-9._-]{0,14}$")
_MAX_NOTE = 140


def _chain_of(address: str) -> str | None:
    if _EVM.match(address):
        return "ethereum"
    if _SOL.match(address):
        return "solana"
    return None


def parse_call(args: list[str]) -> dict[str, str] | None:
    """Return {address, chain, note} for `/call <address> [note...]`, else None.

    A bare `$SYMBOL` is accepted as a token reference; its chain stays empty
    because we will not guess a contract from a ticker.
    """
    if not args:
        return None
    head, rest = args[0].strip(), args[1:]
    chain = _chain_of(head)
    if chain is None:
        if not _SYMBOL.match(head):
            return None
        return {"address": head, "chain": "", "note": " ".join(rest)[:_MAX_NOTE]}
    return {"address": head, "chain": chain, "note": " ".join(rest)[:_MAX_NOTE]}
