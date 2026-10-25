"""Whale Profiler — deep wallet intelligence from our own indexed data.

Given a wallet address, builds a profile:
  - Token holdings (Ankr Advanced multichain balance)
  - Recent transfers (Ankr Advanced)
  - Behavioral classification (sniper, whale, early buyer, etc.)

Free-data-first doctrine: no paid APIs required.

HONESTY CONTRACT (2026-10-25): a provider outage is NOT a small wallet. When
the balance source fails, the profile reports ``size_class="UNKNOWN"`` and
``degraded=True`` with the underlying errors — never a fabricated ``MINNOW``
computed from a placeholder zero balance.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from rugmunchbot.tools._whale_ankr import (
    get_wallet_balance,
    get_wallet_transfers,
    usd,
)

logger = logging.getLogger(__name__)

_EVM_ADDR = re.compile(r"^(?:0x)?[0-9a-fA-F]{40}$")

SIZE_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (10_000_000, "MEGA_WHALE"),
    (1_000_000, "WHALE"),
    (100_000, "DOLPHIN"),
    (10_000, "FISH"),
)


def normalize_wallet(wallet: str) -> str:
    """Trim + EVM-normalize an address without corrupting non-EVM chains.

    A Solana base58 address must NOT gain a ``0x`` prefix — that silently
    queries a different (never-existing) account.
    """
    addr = (wallet or "").strip()
    if _EVM_ADDR.match(addr):
        return addr if addr.startswith("0x") else f"0x{addr}"
    return addr


def classify_whale_profile(
    balance_data: Any,
    transfers: Any,
    *,
    balance_available: bool = True,
) -> dict[str, Any]:
    """Build behavioral classification from balance + transfer data.

    ``balance_available=False`` means the balance source failed: the size
    class is UNKNOWN and the profile is flagged degraded (a zero placeholder
    balance must never be classified as a MINNOW).
    """
    balance = balance_data if isinstance(balance_data, dict) else {}
    rows = [t for t in transfers if isinstance(t, dict)] if isinstance(transfers, list) else []
    total_usd = usd(balance.get("total_usd"))
    token_count = balance.get("token_count")
    token_count = (
        token_count if isinstance(token_count, int) else len(balance.get("top_tokens") or [])
    )

    size_class = "UNKNOWN"
    if balance_available:
        size_class = "MINNOW"
        for floor, label in SIZE_THRESHOLDS:
            if total_usd >= floor:
                size_class = label
                break

    recent_volume = sum(usd(t.get("value_usd")) for t in rows[:10])
    unique_chains = len({str(t.get("chain") or "") for t in rows if t.get("chain")})

    flags: list[str] = []
    if token_count > 20:
        flags.append("multi_chain_degen")
    if recent_volume > 100_000:
        flags.append("high_velocity")
    if len(rows) >= 15 and unique_chains >= 3:
        flags.append("cross_chain_operator")

    return {
        "size_class": size_class,
        "total_portfolio_usd": total_usd if balance_available else None,
        "tokens_held": token_count if balance_available else None,
        "recent_transfer_volume_usd": round(recent_volume, 2),
        "unique_chains_active": unique_chains,
        "behavioral_flags": flags,
        "degraded": not balance_available,
    }


async def profile_whale(wallet: str) -> dict[str, Any]:
    """Full whale profile: balance + transfers + behavioral classification."""
    address = normalize_wallet(wallet)
    errors: list[str] = []

    try:
        balance_data = await get_wallet_balance(address)
        balance_ok = True
    except Exception as exc:
        logger.warning("whale_profiler:balance_failed err=%s", str(exc)[:100])
        errors.append(f"balance: {str(exc)[:120]}")
        balance_data = {"total_usd": 0, "token_count": 0, "top_tokens": []}
        balance_ok = False

    try:
        transfers = await get_wallet_transfers(address, limit=20)
    except Exception as exc:
        logger.warning("whale_profiler:transfers_failed err=%s", str(exc)[:100])
        errors.append(f"transfers: {str(exc)[:120]}")
        transfers = []

    behavior = classify_whale_profile(balance_data, transfers, balance_available=balance_ok)

    return {
        "wallet": address,
        "balance": balance_data,
        "recent_transfers": transfers[:10],
        "behavior": behavior,
        "degraded": bool(errors),
        "errors": errors,
        "profiled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
