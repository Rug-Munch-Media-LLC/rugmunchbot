"""RugMunchBot — shared address-validation utilities (portable).

Port of the pure-regex helpers from the private ``app.core.shared_utils``.
No network, no internal imports — safe for OSS.
"""

from __future__ import annotations

import re

EVM_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
SOLANA_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


def is_evm_address(address: str) -> bool:
    """True for 0x-prefixed 40-char hex strings."""
    return bool(EVM_ADDRESS_RE.match(address or ""))


def is_solana_address(address: str) -> bool:
    """True for base58 strings 32-44 chars (no ambiguous 0OIl chars)."""
    return bool(SOLANA_ADDRESS_RE.match(address or ""))


def is_valid_address(address: str) -> bool:
    """True when *address* is a valid EVM or Solana address."""
    return is_evm_address(address) or is_solana_address(address)


__all__ = ["is_evm_address", "is_solana_address", "is_valid_address"]
