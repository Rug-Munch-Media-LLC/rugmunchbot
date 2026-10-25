"""Entity recognition for promo messages (pure).

Kept apart from ``signals`` so the address vocabulary has exactly one home:
EVM ``0x`` hex and base58 Solana mints, with word boundaries so a link
fragment is never mistaken for a mint.
"""

from __future__ import annotations

import re

_EVM_RE = re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{40}(?![0-9A-Za-z])")
_SOL_RE = re.compile(r"(?<![0-9A-Za-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![0-9A-Za-z])")


def find_entity(text: str) -> str:
    """First contract/mint address in ``text`` ("" when none).

    EVM wins over Solana: ``0x`` is unambiguous, whereas a base58 run can be
    a truncated link fragment.
    """
    evm = _EVM_RE.search(text or "")
    if evm:
        return evm.group(0)
    sol = _SOL_RE.search(text or "")
    return sol.group(0) if sol else ""


def looks_like_address(value: str) -> bool:
    """True for a full EVM address or a plausible base58 mint."""
    token = str(value or "").strip()
    if token.startswith("0x"):
        return bool(_EVM_RE.fullmatch(token))
    return 32 <= len(token) <= 44 and bool(_SOL_RE.fullmatch(token))


def chain_of(entity: str) -> str:
    """Chain implied by the address shape (``ethereum`` / ``solana``)."""
    return "ethereum" if str(entity or "").startswith("0x") else "solana"
